using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace HullOpt
{
    /// <summary>An error reported by the engine, or a failure to reach it.</summary>
    public sealed class EngineException : Exception
    {
        public EngineException(string message, int code = 0, string errorType = null) : base(message)
        {
            Code = code;
            ErrorType = errorType;
        }

        public int Code { get; private set; }
        public string ErrorType { get; private set; }
    }

    /// <summary>
    /// JSON-RPC 2.0 client for the Python engine ("python -m hullopt serve"): one engine process
    /// per Rhino session, one JSON message per line. The engine answers requests one at a time;
    /// cancelling a request stops the engine, and the next request starts a fresh one.
    /// Events are raised on background threads.
    /// </summary>
    public sealed class EngineClient : IDisposable
    {
        private static EngineClient _shared;
        private static readonly object SharedGate = new object();

        private readonly object _gate = new object();
        private readonly object _writeGate = new object();
        private readonly Dictionary<long, TaskCompletionSource<Dictionary<string, object>>> _pending =
            new Dictionary<long, TaskCompletionSource<Dictionary<string, object>>>();
        private Process _process;
        private TaskCompletionSource<Dictionary<string, object>> _ready;
        private long _nextId;
        private bool _disposed;

        public static EngineClient Shared
        {
            get
            {
                lock (SharedGate)
                {
                    if (_shared == null) _shared = new EngineClient();
                    return _shared;
                }
            }
        }

        /// <summary>Stops the shared engine if one was started (Rhino is closing).</summary>
        public static void ShutdownShared()
        {
            EngineClient client;
            lock (SharedGate)
            {
                client = _shared;
                _shared = null;
            }
            if (client != null) client.Dispose();
        }

        public event Action<string> StatusChanged;
        public event Action<long, string, double> Progress;
        /// <summary>A progress notification that carries data (request id, the data object).</summary>
        public event Action<long, Dictionary<string, object>> ProgressData;
        public event Action<string> Log;

        public string Status { get; private set; } = "Not started";
        public Dictionary<string, object> Info { get; private set; }
        public string ProjectFolder { get; private set; }

        /// <summary>
        /// The engine project folder: the HULLOPT_PROJECT environment variable, else the first folder
        /// above the plug-in file that holds .venv and engine\hullopt.
        /// </summary>
        public static string FindProjectFolder()
        {
            var fromEnvironment = Environment.GetEnvironmentVariable("HULLOPT_PROJECT");
            if (!string.IsNullOrEmpty(fromEnvironment) && PythonPath(fromEnvironment) != null)
                return fromEnvironment;
            var folder = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            while (!string.IsNullOrEmpty(folder))
            {
                if (PythonPath(folder) != null && Directory.Exists(Path.Combine(folder, "engine", "hullopt")))
                    return folder;
                folder = Path.GetDirectoryName(folder);
            }
            return null;
        }

        public static string PythonPath(string projectFolder)
        {
            var path = Path.Combine(projectFolder, ".venv", "Scripts", "python.exe");
            return File.Exists(path) ? path : null;
        }

        public bool IsRunning
        {
            get { lock (_gate) return _process != null; }
        }

        /// <summary>Starts the engine if needed; completes with its info once it is ready.</summary>
        public Task<Dictionary<string, object>> StartAsync()
        {
            lock (_gate)
            {
                if (_disposed) throw new ObjectDisposedException("EngineClient");
                if (_process != null && _ready != null) return _ready.Task;
                return StartLocked();
            }
        }

        private Task<Dictionary<string, object>> StartLocked()
        {
            var ready = new TaskCompletionSource<Dictionary<string, object>>(TaskCreationOptions.RunContinuationsAsynchronously);
            var project = FindProjectFolder();
            if (project == null)
            {
                ready.SetException(new EngineException(
                    "Engine not found: no folder with .venv and engine\\hullopt above the plug-in, and HULLOPT_PROJECT is not set."));
                return ready.Task;
            }
            ProjectFolder = project;
            var info = new ProcessStartInfo
            {
                FileName = PythonPath(project),
                Arguments = "-m hullopt serve",
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WorkingDirectory = project,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8,
            };
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
            info.EnvironmentVariables["MPLBACKEND"] = "Agg";
            Process process;
            try
            {
                process = Process.Start(info);
            }
            catch (Exception ex)
            {
                ready.SetException(new EngineException("Could not start the engine: " + ex.Message));
                return ready.Task;
            }
            process.StandardInput.AutoFlush = true;
            _process = process;
            _ready = ready;
            new Thread(() => ReadLoop(process)) { IsBackground = true, Name = "HullOpt engine output" }.Start();
            new Thread(() => ErrorLoop(process)) { IsBackground = true, Name = "HullOpt engine log" }.Start();
            SetStatus("Starting");
            Task.Delay(TimeSpan.FromSeconds(90)).ContinueWith(_ =>
            {
                if (ready.TrySetException(new EngineException("The engine did not start within 90 s.")))
                    Kill(process);
            });
            return ready.Task;
        }

        /// <summary>Sends one request and completes with its result, or fails with an EngineException.</summary>
        public Task<Dictionary<string, object>> CallAsync(string method, Dictionary<string, object> parameters,
                                                         CancellationToken cancel)
        {
            return CallAsync(method, parameters, cancel, null);
        }

        /// <summary>As CallAsync; <paramref name="sent"/> receives the request id once the request is on its way.</summary>
        public async Task<Dictionary<string, object>> CallAsync(string method, Dictionary<string, object> parameters,
                                                               CancellationToken cancel, Action<long> sent)
        {
            await StartAsync().ConfigureAwait(false);
            var completion = new TaskCompletionSource<Dictionary<string, object>>(TaskCreationOptions.RunContinuationsAsynchronously);
            Process process;
            long id;
            lock (_gate)
            {
                process = _process;
                if (process == null) throw new EngineException("The engine is not running.");
                id = ++_nextId;
                _pending[id] = completion;
            }
            var request = new Dictionary<string, object>
            {
                { "jsonrpc", "2.0" },
                { "id", id },
                { "method", method },
                { "params", parameters ?? new Dictionary<string, object>() },
            };
            var text = Json.Serialize(request);
            try
            {
                lock (_writeGate) process.StandardInput.WriteLine(text);
            }
            catch (Exception ex)
            {
                lock (_gate) _pending.Remove(id);
                throw new EngineException("Could not send the request to the engine: " + ex.Message);
            }
            SetStatus("Busy");
            if (sent != null) sent(id);
            using (cancel.Register(() =>
            {
                if (completion.TrySetCanceled()) Kill(process);
            }))
            {
                return await completion.Task.ConfigureAwait(false);
            }
        }

        private void ReadLoop(Process process)
        {
            try
            {
                string line;
                while ((line = process.StandardOutput.ReadLine()) != null)
                {
                    if (line.Length == 0) continue;
                    Dictionary<string, object> message = null;
                    try
                    {
                        message = Json.Parse(line) as Dictionary<string, object>;
                    }
                    catch (Exception)
                    {
                        RaiseLog("Unreadable engine output: " + line);
                    }
                    if (message != null) Dispatch(process, message);
                }
            }
            catch (Exception ex)
            {
                RaiseLog("Engine output stopped: " + ex.Message);
            }
            Exited(process);
        }

        private void ErrorLoop(Process process)
        {
            try
            {
                string line;
                while ((line = process.StandardError.ReadLine()) != null)
                    RaiseLog(line);
            }
            catch (Exception)
            {
                // The process is gone; the output loop reports it.
            }
        }

        private void Dispatch(Process process, Dictionary<string, object> message)
        {
            object methodValue;
            if (message.TryGetValue("method", out methodValue))
            {
                var method = methodValue as string;
                var parameters = J.Obj(message, "params");
                if (method == "ready")
                {
                    TaskCompletionSource<Dictionary<string, object>> ready;
                    lock (_gate)
                    {
                        if (process != _process) return;
                        Info = parameters;
                        ready = _ready;
                    }
                    SetStatus("Ready");
                    if (ready != null) ready.TrySetResult(parameters ?? new Dictionary<string, object>());
                }
                else if (method == "progress")
                {
                    var requestId = (long)J.Num(parameters, "request_id");
                    var handler = Progress;
                    if (handler != null) handler(requestId, J.Str(parameters, "stage"), J.Num(parameters, "fraction"));
                    var data = J.Obj(parameters, "data");
                    var dataHandler = ProgressData;
                    if (data != null && dataHandler != null) dataHandler(requestId, data);
                }
                return;
            }
            object idValue;
            if (!message.TryGetValue("id", out idValue) || idValue == null)
            {
                RaiseLog("Engine message without a request id: " + Json.Serialize(message));
                return;
            }
            long id;
            try
            {
                id = Convert.ToInt64(idValue, CultureInfo.InvariantCulture);
            }
            catch (Exception)
            {
                return;
            }
            TaskCompletionSource<Dictionary<string, object>> pending;
            bool idle;
            lock (_gate)
            {
                if (!_pending.TryGetValue(id, out pending)) return;
                _pending.Remove(id);
                idle = _pending.Count == 0;
            }
            var error = J.Obj(message, "error");
            if (error != null)
            {
                var code = J.Num(error, "code");
                pending.TrySetException(new EngineException(J.Str(error, "message") ?? "Engine error",
                                                            double.IsNaN(code) ? 0 : (int)code,
                                                            J.Str(J.Obj(error, "data"), "type")));
            }
            else
            {
                pending.TrySetResult(J.Obj(message, "result") ?? new Dictionary<string, object>());
            }
            if (idle) SetStatus("Ready");
        }

        private void Exited(Process process)
        {
            List<TaskCompletionSource<Dictionary<string, object>>> orphans;
            TaskCompletionSource<Dictionary<string, object>> ready = null;
            lock (_gate)
            {
                if (process != _process) return;
                _process = null;
                ready = _ready;
                _ready = null;
                orphans = new List<TaskCompletionSource<Dictionary<string, object>>>(_pending.Values);
                _pending.Clear();
            }
            var code = "";
            try
            {
                if (process.HasExited) code = " (exit code " + process.ExitCode + ")";
            }
            catch (Exception)
            {
                // Exit code unavailable.
            }
            var failure = new EngineException("The engine stopped" + code + ".");
            if (ready != null) ready.TrySetException(failure);
            foreach (var orphan in orphans) orphan.TrySetException(failure);
            SetStatus("Stopped");
        }

        private static void Kill(Process process)
        {
            try
            {
                if (!process.HasExited) process.Kill();
            }
            catch (Exception)
            {
                // Already gone.
            }
        }

        /// <summary>Stops the engine; the next request starts a new one.</summary>
        public void Restart()
        {
            Process process;
            lock (_gate) process = _process;
            if (process != null) Kill(process);
        }

        private void SetStatus(string status)
        {
            Status = status;
            var handler = StatusChanged;
            if (handler != null) handler(status);
        }

        private void RaiseLog(string line)
        {
            var handler = Log;
            if (handler != null) handler(line);
        }

        public void Dispose()
        {
            Process process;
            lock (_gate)
            {
                if (_disposed) return;
                _disposed = true;
                process = _process;
            }
            if (process == null) return;
            try
            {
                lock (_writeGate)
                    process.StandardInput.WriteLine("{\"jsonrpc\":\"2.0\",\"id\":0,\"method\":\"engine.shutdown\"}");
                if (!process.WaitForExit(2000)) Kill(process);
            }
            catch (Exception)
            {
                Kill(process);
            }
        }
    }
}

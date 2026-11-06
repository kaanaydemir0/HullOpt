using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using System.Web.Script.Serialization;

namespace HullOpt
{
    /// <summary>
    /// JSON through the .NET Framework serializer. Output is escaped to ASCII, so what is
    /// written to the engine's standard input never depends on the console code page
    /// (folder names may contain non-ASCII letters).
    /// </summary>
    public static class Json
    {
        private static JavaScriptSerializer Serializer()
        {
            return new JavaScriptSerializer { MaxJsonLength = int.MaxValue, RecursionLimit = 512 };
        }

        public static string Serialize(object value)
        {
            return Ascii(Serializer().Serialize(value));
        }

        public static object Parse(string text)
        {
            return Serializer().DeserializeObject(text);
        }

        public static T Deserialize<T>(string text)
        {
            return Serializer().Deserialize<T>(text);
        }

        public static string Ascii(string text)
        {
            var result = new StringBuilder(text.Length + 16);
            foreach (var c in text)
            {
                if (c < 128) result.Append(c);
                else result.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
            }
            return result.ToString();
        }

        /// <summary>A JSON string literal, ASCII only.</summary>
        public static string Quote(string text)
        {
            var result = new StringBuilder((text ?? "").Length + 2);
            result.Append('"');
            foreach (var c in text ?? "")
            {
                switch (c)
                {
                    case '"': result.Append("\\\""); break;
                    case '\\': result.Append("\\\\"); break;
                    case '\n': result.Append("\\n"); break;
                    case '\r': result.Append("\\r"); break;
                    case '\t': result.Append("\\t"); break;
                    default:
                        if (c < 32 || c > 126) result.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        else result.Append(c);
                        break;
                }
            }
            return result.Append('"').ToString();
        }

        /// <summary>A JSON number that reads back to the same double.</summary>
        public static string Number(double value)
        {
            if (double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentException("JSON numbers must be finite.");
            return value.ToString("R", CultureInfo.InvariantCulture);
        }
    }

    /// <summary>Readers for parsed JSON: objects are dictionaries, arrays are lists, numbers any numeric type.</summary>
    public static class J
    {
        public static Dictionary<string, object> Obj(Dictionary<string, object> parent, string key)
        {
            object value;
            return parent != null && parent.TryGetValue(key, out value) ? value as Dictionary<string, object> : null;
        }

        public static IList List(Dictionary<string, object> parent, string key)
        {
            object value;
            return parent != null && parent.TryGetValue(key, out value) ? value as IList : null;
        }

        public static string Str(Dictionary<string, object> parent, string key)
        {
            object value;
            if (parent == null || !parent.TryGetValue(key, out value) || value == null) return null;
            return Convert.ToString(value, CultureInfo.InvariantCulture);
        }

        public static double Num(object value)
        {
            if (value == null || value is string || value is bool) return double.NaN;
            try
            {
                return Convert.ToDouble(value, CultureInfo.InvariantCulture);
            }
            catch (InvalidCastException) { return double.NaN; }
            catch (FormatException) { return double.NaN; }
            catch (OverflowException) { return double.NaN; }
        }

        public static double Num(Dictionary<string, object> parent, string key)
        {
            object value;
            return parent != null && parent.TryGetValue(key, out value) ? Num(value) : double.NaN;
        }

        public static double[] Nums(IList list)
        {
            if (list == null) return new double[0];
            var result = new double[list.Count];
            for (var i = 0; i < result.Length; i++) result[i] = Num(list[i]);
            return result;
        }

        public static double[] Nums(Dictionary<string, object> parent, string key)
        {
            return Nums(List(parent, key));
        }

        public static List<string> Strs(Dictionary<string, object> parent, string key)
        {
            var result = new List<string>();
            var list = List(parent, key);
            if (list == null) return result;
            foreach (var item in list)
                if (item != null) result.Add(Convert.ToString(item, CultureInfo.InvariantCulture));
            return result;
        }
    }
}

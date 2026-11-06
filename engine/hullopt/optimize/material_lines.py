"""Exact NURBS material addresses and common 3D arc-length fairness metrics.

Plane cuts establish reference lines only. Each material sample stores face/u/v;
candidate points are evaluated on that face, not recut or interpolated in 3D.
Fixed weights/knots make the cached CV-mode superposition exact at these UVs.
"""
from dataclasses import dataclass
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from .fairness import LineSet, FairnessPlan, H, inflections
from ..geometry.nurbs import evaluate_surface
from ..hydro.sections import plane_segments


def curvature_3d(points, reference_normal, spacing_m=H, smoothing_m=.08):
    """Signed 3D curvature at original material samples, after physical filtering.

    Magnitude is ||r' x r''||/||r'||^3. Sign uses the fixed reference plane's
    normal; no projected 2D curvature is substituted for the 3D magnitude.
    """
    p=np.asarray(points,float)
    if p.ndim!=2 or p.shape[1]!=3 or len(p)<5 or not np.isfinite(p).all():
        raise ValueError('Need at least five finite 3D material samples')
    arc=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(p,axis=0),axis=1))]
    keep=np.r_[True,np.diff(arc)>1e-12]
    if keep.sum()<5 or arc[-1]<=0:raise ValueError('Degenerate material curve')
    s=np.linspace(0,arc[-1],max(5,int(np.ceil(arc[-1]/spacing_m))+1))
    ds=float(s[1]-s[0]);q=np.stack([np.interp(s,arc[keep],p[keep,i]) for i in range(3)],axis=1)
    q=gaussian_filter1d(q,smoothing_m/ds,axis=0,mode='nearest')
    d=np.gradient(q,ds,axis=0);dd=np.gradient(d,ds,axis=0)
    cross=np.cross(d,dd);speed=np.linalg.norm(d,axis=1)
    k=np.linalg.norm(cross,axis=1)/np.maximum(speed**3,1e-12)
    k*=np.where(cross@np.asarray(reference_normal)>=0,1.,-1.)
    return np.interp(arc,s,k),arc


def energy(k,arc,mask):
    # Arc lengths are physical candidate distances; exclude zero-length pole
    # intervals rather than amplify floating-point duplicate coordinates.
    keep=np.r_[True,np.diff(arc)>1e-10]
    k,arc,mask=k[keep],arc[keep],mask[keep]
    if len(k)<3:return 0.
    derivative=np.gradient(k,arc)
    return float(np.trapezoid(np.where(mask,derivative**2,0.),arc))


@dataclass
class MaterialCurve:
    family: str
    label: str
    face_ids: np.ndarray
    uv: np.ndarray
    points: np.ndarray
    normal: np.ndarray
    curvature: np.ndarray
    arc: np.ndarray
    mask: np.ndarray

    @property
    def p(self):return self.points


class MaterialLineSet:
    def __init__(self,curves,plan=None):self.curves=curves;self.plan=plan

    @classmethod
    def from_reference(cls,geometry,mesh,frame,plan=None):
        if mesh.uv_triangles is None:raise ValueError('Reference tessellation requires include_uv=True')
        plan=plan or FairnessPlan.from_frame(frame)
        reference=LineSet.from_mesh(mesh.triangles,frame,plan=plan)
        curves=[];mapping_error=[]
        for c in reference.curves:
            axis={'st':0,'wl':2,'bt':1}[c.family]
            val=float(c.label.split()[1].split('#')[0])
            # Labels are rounded to mm; the actual cut coordinate is recovered
            # from the plane-constant reference points, not the display label.
            val=float(np.median(c.p[:,axis]))
            seg,ids=plane_segments(mesh.triangles,val,axis)
            k=min(20,len(seg));_,near=cKDTree(seg.mean(axis=1)).query(c.p,k=k)
            if k==1:near=near[:,None]
            a,b=seg[near,0],seg[near,1];edge=b-a;rel=c.p[:,None,:]-a
            t=np.clip(np.divide(np.sum(rel*edge,axis=2),np.sum(edge*edge,axis=2),out=np.zeros(near.shape),where=np.sum(edge*edge,axis=2)>0),0,1)
            distance=np.linalg.norm(rel-t[:,:,None]*edge,axis=2)
            best=distance.argmin(axis=1);err=distance[np.arange(len(c.p)),best]
            chosen=near[np.arange(len(c.p)),best]
            # A long segment can have a distant centroid while passing through
            # the point. Exhaustively resolve only the missed centroid queries.
            ea=seg[:,0];ed=seg[:,1]-ea;den=np.sum(ed*ed,axis=1)
            for j in np.flatnonzero(err>2e-6):
                delta=c.p[j]-ea
                frac=np.clip(np.divide(np.sum(delta*ed,axis=1),den,out=np.zeros(len(seg)),where=den>0),0,1)
                dd=np.linalg.norm(delta-frac[:,None]*ed,axis=1)
                chosen[j]=dd.argmin();err[j]=dd[chosen[j]]
            if err.max()>2e-6:raise ValueError('Material reference not on cut segments '+c.label+': '+str(err.max()))
            ti=ids[chosen];tri=mesh.triangles[ti];tu=mesh.uv_triangles[ti]
            # Cut-edge barycentric weights avoid the ill-conditioned 3D Gram
            # system on very thin trim triangles. Both endpoint UVs and the
            # interpolation along the cut stay convex within the UV triangle.
            endpoints=[];valid=[]
            for a,b in ((0,1),(1,2),(2,0)):
                cross=(tri[:,a,axis]<=val)!=(tri[:,b,axis]<=val)
                fraction=np.divide(val-tri[:,a,axis],tri[:,b,axis]-tri[:,a,axis],out=np.zeros(len(ti)),where=cross)
                endpoints.append(tu[:,a]+fraction[:,None]*(tu[:,b]-tu[:,a]));valid.append(cross)
            endpoints=np.stack(endpoints,axis=1);valid=np.stack(valid,axis=1)
            cut_uv=endpoints[valid].reshape(-1,2,2)
            selected_seg=seg[chosen];edge=selected_seg[:,1]-selected_seg[:,0]
            fraction=np.clip(np.sum((c.p-selected_seg[:,0])*edge,axis=1)/np.sum(edge*edge,axis=1),0,1)
            uv=cut_uv[:,0]+fraction[:,None]*(cut_uv[:,1]-cut_uv[:,0]);faces=mesh.face_ids[ti]
            p=cls.evaluate_addresses(geometry,faces,uv)
            normal=np.cross(np.eye(3)[c.plane[0]],np.eye(3)[c.plane[1]])
            kap,arc=curvature_3d(p,normal,plan.spacing_m,plan.smoothing_m)
            mask=(np.abs(kap)<plan.knuckle_curvature)&(p[:,0]>plan.end_mask_x)
            mask[:12]=False;mask[-12:]=False
            curves.append(MaterialCurve(c.family,c.label,faces,uv,p,normal,kap,arc,mask))
            mapping_error.append(float(np.linalg.norm(p-c.p,axis=1).max()))
        if not curves:raise ValueError('Empty material reference')
        result=cls(curves,plan);result.mapping_diagnostic={'curves':len(curves),'samples':sum(len(c.points) for c in curves),
            'maximum_reference_mesh_chord_to_NURBS_m':max(mapping_error),'candidate_evaluation':'Exact NURBS at face/u/v'}
        return result

    @staticmethod
    def evaluate_addresses(geometry,face_ids,uv):
        out=np.empty((len(uv),3))
        for fid in np.unique(face_ids):
            mask=face_ids==fid
            out[mask]=evaluate_surface(geometry.faces[int(fid)].surface,uv[mask,0],uv[mask,1])
        return out

    def points_on(self,geometry):
        return [self.evaluate_addresses(geometry,c.face_ids,c.uv) for c in self.curves]

    def precompute(self,geometry,frame,names,apply_fn):
        self.names=tuple(names);self.mode_points=[]
        for name in names:
            deformed=apply_fn(geometry,frame,{name:1.})
            self.mode_points.append([q-c.points for q,c in zip(self.points_on(deformed),self.curves)])
        self.D=[np.stack([rows[i] for rows in self.mode_points]) for i in range(len(self.curves))]
        del self.mode_points

    def deformed_points(self,amps):
        a=np.array([amps.get(n,0.) for n in self.names])
        return [c.points+np.tensordot(a,d,axes=1) for c,d in zip(self.curves,self.D)]

    def evaluate(self,points,detail=False,plan=None):
        if len(points)!=len(self.curves):raise ValueError('Material curve coverage mismatch')
        plan=plan or self.plan
        if plan is None:raise ValueError('Fairness evaluation needs a FairnessPlan')
        low,high=plan.ratio
        worst=ratio_bad=0.;new=0;radius=1.;where=None;rows=[]
        e0={f:0. for f in ['st','wl','bt']};e1=dict(e0)
        for c,q in zip(self.curves,points):
            if np.asarray(q).shape!=c.points.shape:raise ValueError('Material sample coverage mismatch')
            k,arc=curvature_3d(q,c.normal,plan.spacing_m,plan.smoothing_m);m=c.mask
            dk=np.where(m,np.abs(k-c.curvature),0.);i=int(dk.argmax())
            if dk[i]>worst:worst=float(dk[i]);where=(c.label,c.points[i].tolist())
            big=m&(np.abs(c.curvature)>plan.ratio_min_curvature)
            if big.any():
                ratio=k[big]/c.curvature[big];ratio_bad=max(ratio_bad,float(np.maximum(low-ratio,ratio-high).max()))
            # Uniform material reference spacing; both paths use identical
            # persistence and 3D curvature. No candidate-dependent masking.
            thr=plan.inflection_threshold
            n0=inflections(c.curvature,m,thr);n1=inflections(k,m,thr);new+=max(0,n1-n0)
            bilge=(m&(c.family=='st')&(c.points[:,0]>=plan.bilge_x[0])&(c.points[:,0]<=plan.bilge_x[1])
                   &(c.points[:,2]<plan.bilge_z_max)&(np.abs(c.curvature)>plan.ratio_min_curvature))
            if bilge.any():radius=min(radius,float(np.min(np.abs(c.curvature[bilge])/np.maximum(np.abs(k[bilge]),1e-12))))
            e0[c.family]+=energy(c.curvature,c.arc,m);e1[c.family]+=energy(k,arc,m)
            if detail:rows.append({'label':c.label,'dk_max':float(dk[i]),'infl0':n0,'infl1':n1,'at':c.points[i].tolist()})
        er={f:e1[f]/e0[f] if e0[f]>1e-12 else 1.+e1[f]/1e-12 for f in e0}
        out=dict(dk_max=worst,dk_where=where,new_infl=new,ratio_excess=max(0.,ratio_bad),energy_ratio=er,
                 bilge_radius_ratio_min=radius,pass_G1=new==0,pass_G2=worst<=plan.dk_max and ratio_bad<=0,
                 pass_G3=all(v<=plan.energy_max+1e-12 for v in er.values()),pass_G5=radius>=plan.bilge_radius_min-1e-12)
        out['pass']=all(out[k] for k in ['pass_G1','pass_G2','pass_G3','pass_G5'])
        if detail:out['curves']=rows
        return out

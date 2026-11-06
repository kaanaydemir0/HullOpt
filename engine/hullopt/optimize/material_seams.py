"""Precomputed NURBS seam position/derivative modes for native CAD gates."""
import numpy as np
from .material_lines import MaterialLineSet
from .shape_modes_v2 import MODE_NAMES_V2,apply_modes_v2


def derivatives(geometry,ids,uv):
    out=[]
    for axis in (0,1):
        lo=uv.copy();hi=uv.copy()
        for fid in np.unique(ids):
            m=ids==fid;s=geometry.faces[int(fid)].surface
            domain=s.domain_u if axis==0 else s.domain_v
            step=(domain[1]-domain[0])*1e-5
            lo[m,axis]=np.maximum(domain[0],uv[m,axis]-step)
            hi[m,axis]=np.minimum(domain[1],uv[m,axis]+step)
        delta=MaterialLineSet.evaluate_addresses(geometry,ids,hi)-MaterialLineSet.evaluate_addresses(geometry,ids,lo)
        out.append(delta/(hi[:,axis]-lo[:,axis])[:,None])
    return np.stack(out)


class MaterialSeams:
    def __init__(self,geometry,frame,native):
        rows=[];tangent=[]
        for pair in native['seams']['pairs']:
            for row in pair['material_uv_samples']:
                rows.append(row);tangent.append(pair['angle_in_deg']<10)
        self.ids=np.array([[r['faceA'],r['faceB']] for r in rows]).ravel()
        self.uv=np.array([[r['uvA'],r['uvB']] for r in rows]).reshape(-1,2)
        self.tangent=np.array(tangent);self.names=MODE_NAMES_V2
        self.p0=MaterialLineSet.evaluate_addresses(geometry,self.ids,self.uv)
        self.d0=derivatives(geometry,self.ids,self.uv)
        self.P=[];self.D=[]
        for name in self.names:
            g=apply_modes_v2(geometry,frame,{name:1.})
            self.P.append(MaterialLineSet.evaluate_addresses(g,self.ids,self.uv)-self.p0)
            self.D.append(derivatives(g,self.ids,self.uv)-self.d0)
        self.P=np.stack(self.P);self.D=np.stack(self.D)
        self.angle0=self.angles(self.d0)

    @staticmethod
    def angles(d):
        n=np.cross(d[0],d[1]);n/=np.maximum(np.linalg.norm(n,axis=1),1e-300)[:,None]
        return np.degrees(np.arccos(np.clip(np.sum(n[::2]*n[1::2],axis=1),-1,1)))

    def evaluate(self,amps):
        a=np.array([amps.get(n,0.) for n in self.names])
        p=self.p0+np.tensordot(a,self.P,axes=1);d=self.d0+np.tensordot(a,self.D,axes=1)
        gap=np.linalg.norm(p[::2]-p[1::2],axis=1)
        change=np.abs(self.angles(d)-self.angle0)
        cond=np.linalg.norm(np.cross(d[0],d[1]),axis=1)/np.maximum(np.linalg.norm(d[0],axis=1)*np.linalg.norm(d[1],axis=1),1e-300)
        return {'maximum_gap_m':float(gap.max()),
                'maximum_tangent_angle_change_deg':float(change[self.tangent].max(initial=0.)),
                'new_singular_samples':int(np.sum(cond<1e-8)),
                'method':'Exact CV-linear NURBS positions; finite-difference NURBS tangents at native-audit material UVs'}

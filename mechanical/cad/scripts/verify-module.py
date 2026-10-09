"""Reopen saved FCStd and STEP; report every volumetric assembly interference."""
import json
from pathlib import Path
import FreeCAD as App
import Part

root=Path(__file__).resolve().parents[3]/'mechanical'
doc=App.openDocument(str(root/'cad/assembly/kulager-module-rA-concept.FCStd'))
doc.recompute()
objects=[o for o in doc.Objects if o.TypeId=='PartDesign::Feature' and o.Name!='WithersClearance' and not o.Name.startswith('Strap')]
assert all(o.Shape.isValid() and o.Shape.Volume>0 for o in objects)
overlaps=[]
for i,a in enumerate(objects):
    for b in objects[i+1:]:
        if not a.Shape.BoundBox.intersect(b.Shape.BoundBox): continue
        vol=a.Shape.common(b.Shape).Volume
        if vol>0.01: overlaps.append({'a':a.Name,'b':b.Name,'overlap_mm3':round(vol,3)})
step=Part.Shape(); step.read(str(root/'exports/kulager-module-rA-concept.step'))
expected=sum(o.Shape.Volume for o in objects)
assert step.isValid()
assert abs(step.Volume-expected)<0.1,(step.Volume,expected)
report={'reopened_fcstd_valid':True,'step_valid':True,'step_solids':len(step.Solids),
        'step_volume_mm3':step.Volume,'expected_volume_mm3':expected,'interferences':overlaps}
(root/'calculations/interferences-rA-concept.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
assert not overlaps, 'Unexpected volumetric overlaps; see saved report'

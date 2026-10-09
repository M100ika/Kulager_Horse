"""Verify saved FreeCAD archives and STEP round trips for all saddle templates."""
import json
import zipfile
from pathlib import Path
import FreeCAD as App
import Part

M=Path(__file__).resolve().parents[3]/'mechanical'
reports=[]
for path in sorted((M/'cad/assembly/saddle-templates').glob('*.FCStd')):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert 'Document.xml' in archive.namelist()
        assert 'GuiDocument.xml' in archive.namelist()
    d=App.openDocument(str(path));d.recompute()
    shapes=[o for o in d.Objects if o.TypeId=='PartDesign::Feature']
    assert shapes and all(o.Shape.isValid() and o.Shape.Volume>0 for o in shapes),path.name
    row={'file':path.name,'objects':len(shapes),'archive_valid':True,'reopened_brep_valid':True}
    step_path=M/'exports/saddle-templates'/(path.stem+'.step')
    if step_path.exists():
        physical=[o for name in ['Saddle','Mount','Module','Power','Guards'] for o in d.getObject(name).Group]
        expected=sum(solid.Volume for o in physical for solid in o.Shape.Solids)
        step=Part.Shape();step.read(str(step_path))
        # Compare the same integration granularity: OCC compound mass integration
        # differs slightly from the sum of individually integrated curved solids.
        actual=sum(solid.Volume for solid in step.Solids)
        assert len(step.Solids)==sum(len(o.Shape.Solids) for o in physical)
        assert step.isValid() and abs(actual-expected)<max(.1,expected*1e-8),(path.name,actual,expected)
        row.update({'step_valid':True,'step_solids':len(step.Solids),'volume_error_mm3':round(abs(actual-expected),6)})
    reports.append(row);App.closeDocument(d.Name)
checks=json.loads((M/'calculations/saddle-templates-check-rB-templates.json').read_text())
assert len(checks)==4
assert all(not r['saddle_payload_interferences'] for r in checks)
assert checks[1]['rider_reference_intrusion_mm3']>0, 'Expected bridge intrusion must remain reported'
(M/'calculations/saddle-templates-roundtrip-rB-templates.json').write_text(json.dumps(reports,indent=2))
print(json.dumps(reports,indent=2))

"""FreeCAD 1.1: reproducible concept assembly, STEP, meshes and validation.

Run using FreeCAD's Python, or exec(compile(open(path).read(), path, 'exec'))
in the FreeCAD Python console. KULAGER_CAD_ROOT may override repository path.
Geometry is rebuilt from layout-parameters.json; spreadsheet is a snapshot,
not a live feature driver. No vendor interfaces or safety devices are certified.
"""
import csv
import json
import math
import os
from pathlib import Path

import FreeCAD as App
import Part
import MeshPart

if os.environ.get('KULAGER_GUI') == '1':
    import FreeCADGui as Gui
    Gui.showMainWindow()

ROOT = Path(os.environ.get('KULAGER_CAD_ROOT', Path(__file__).resolve().parents[3]))
HERE = ROOT / 'mechanical'
P = json.loads((HERE / 'cad/scripts/layout-parameters.json').read_text())
REV = P['revision']
for directory in ['cad/assembly', 'exports', 'drawings', 'calculations']:
    (HERE / directory).mkdir(parents=True, exist_ok=True)
if 'KulagerModule' in App.listDocuments():
    App.closeDocument('KulagerModule')
doc = App.newDocument('KulagerModule')
doc.Label = 'KULAGER — rA CONCEPT / стендовая компоновка'
V = App.Vector
colors = {'metal': (0.68, 0.73, 0.78), 'motor': (0.12, 0.16, 0.20),
          'orange': (0.96, 0.48, 0.12), 'blue': (0.12, 0.48, 0.70),
          'green': (0.13, 0.55, 0.40), 'red': (0.85, 0.12, 0.13),
          'rubber': (0.19, 0.21, 0.24), 'cover': (0.70, 0.82, 0.88)}
groups = {}
for name, label in [('Mount','01 / Регулируемая рама'), ('Drive','02 / Два привода'),
                    ('Reins','03 / Выходы поводьев'), ('Power','04 / Аккумулятор'),
                    ('Control','05 / Электроника'), ('Cover','06 / Кожух'),
                    ('Reference','07 / Условные зоны и трассы')]:
    groups[name] = doc.addObject('App::DocumentObjectGroup', name)
    groups[name].Label = label
items = []

def box(x,y,z,w,d,h):
    return Part.makeBox(w,d,h,V(x,y,z))

def cyl(x,y,z,r,h):
    return Part.makeCylinder(r,h,V(x,y,z))

def ring(x,y,z,ro,ri,h):
    return cyl(x,y,z,ro,h).cut(cyl(x,y,z-1,ri,h+2))

def rounded(x,y,z,w,d,h,r):
    s = box(x+r,y,z,w-2*r,d,h).fuse(box(x,y+r,z,w,d-2*r,h))
    for cx in [x+r,x+w-r]:
        for cy in [y+r,y+d-r]:
            s = s.fuse(cyl(cx,cy,z,r,h))
    return s.removeSplitter()

def slot(x,y,z,length,width,h):
    return rounded(x-length/2,y-width/2,z,length,width,h,width/2-0.001)

def obj(name,label,shape,group,color='metal',kind='concept part',mass=None,visible=True):
    o = doc.addObject('PartDesign::Feature',name)
    o.Label = label
    o.Shape = shape
    o.addProperty('App::PropertyString','Status','Engineering').Status = kind
    o.addProperty('App::PropertyString','Revision','Engineering').Revision = REV
    if mass is not None:
        o.addProperty('App::PropertyFloat','EstimatedMassKg','Engineering').EstimatedMassKg = mass
    groups[group].addObject(o)
    if o.ViewObject:
        o.ViewObject.ShapeColor = colors[color]
        o.ViewObject.LineColor = (0.12,0.15,0.18)
        o.ViewObject.Visibility = visible
    items.append((o,group,color,mass,visible))
    return o

w,d,t,z = [P[k] for k in ['base_width_mm','base_depth_mm','base_thickness_mm','base_z_mm']]
base = rounded(-w/2,-d/2,z,w,d,t,15)
for x in [-115,115]:
    for y in [-90,105]:
        base = base.cut(slot(x,y,z-1,48,8.5,t+2))
# Motor adapters attach to base on four M5 clearance holes per side.
for x in [-P['motor_spacing_mm']/2,P['motor_spacing_mm']/2]:
    for dx in [-40,40]:
        for dy in [-40,40]:
            base = base.cut(cyl(x+dx,P['motor_y_mm']+dy,z-1,2.75,t+2))
obj('Base','01.1 / Несущая плита Al 6061, 6 мм',base,'Mount',mass=base.Volume*2.7e-6)
for side,x in [('L',-P['mount_spacing_mm']/2),('R',P['mount_spacing_mm']/2)]:
    rail = box(x-18,-113,38,36,238,6)
    for y in [-90,105]:
        rail = rail.cut(slot(x,y,37,25,8.5,8))
        obj('MountSpacer'+side+str(abs(y)),f'01.2 / {side} проставка M8',ring(x,y,44,9,4.25,16),'Mount')
        # Visible fastener is a positioning reference, threads not modeled.
        obj('MountBolt'+side+str(abs(y)),f'01.3 / {side} M8 условный',
            cyl(x,y,37,4,30).fuse(cyl(x,y,67,7,5)),'Mount',kind='fastener envelope')
    obj('Rail'+side,f'01.4 / {side} продольная рейка',rail,'Mount',mass=rail.Volume*2.7e-6)
    for suffix,y in [('F',-87),('B',91)]:
        shoe=rounded(x-26,y-22,14,52,44,24,6)
        shoe=shoe.cut(box(x-28,y-13,22,56,26,6))
        shoe=shoe.cut(cyl(x,-90 if suffix=='F' else 105,34,4.25,5))
        obj('SaddleShoe'+side+suffix,f'01.5 / {side}{suffix} адаптер к ленчику — подобрать',shoe,'Mount','blue',kind='saddle-dependent placeholder')
        obj('Pad'+side+suffix,f'01.6 / {side}{suffix} прокладка к седлу',rounded(x-26,y-22,8,52,44,6,6),'Mount','rubber')
        # Strap path through the adapter. Ends must attach to approved tree points.
        strap = box(x-42,y-12,24,84,24,2)
        obj('Strap'+side+suffix,f'01.7 / {side}{suffix} стропа 25 мм, маршрут',strap,'Reference','orange',kind='routing only')

for side,x in [('L',-P['motor_spacing_mm']/2),('R',P['motor_spacing_mm']/2)]:
    y=P['motor_y_mm']; mz=z+t+4
    adapter=rounded(x-61,y-53,z+t,122,106,4,8)
    for dx in [-40,40]:
        for dy in [-40,40]: adapter=adapter.cut(cyl(x+dx,y+dy,z+t-1,2.75,6))
    obj('MotorAdapter'+side,f'02.1 / {side} адаптер — сверловка AK80 TBD',adapter,'Drive',kind='blank; vendor interface NOT drilled')
    obj('Motor'+side,f'02.2 / {side} AK80-64 Ø98 × 61.9',cyl(x,y,mz,P['motor_diameter_mm']/2,P['motor_height_mm']),
        'Drive','motor','vendor envelope, not vendor STEP',0.850)
    top=mz+P['motor_height_mm']
    obj('ReleaseClutch'+side,f'02.3 / {side} муфта размыкания NC-off, РЕЗЕРВ',cyl(x,y,top,35,20),
        'Drive','orange','unselected normally-disengaged clutch envelope',0.20)
    obj('TorqueLimiter'+side,f'02.4 / {side} ограничитель момента, РЕЗЕРВ',cyl(x,y,top+20,29,8),
        'Drive','blue','unselected slip limiter envelope',0.08)
    dz=top+28; rr=P['drum_radius_mm']; dw=P['drum_width_mm']
    drum=cyl(x,y,dz,rr+7,2).fuse(cyl(x,y,dz+2,rr,dw)).fuse(cyl(x,y,dz+dw+2,rr+7,2))
    drum=drum.cut(cyl(x,y,dz-1,6,dw+6))
    obj('Drum'+side,f'02.5 / {side} барабан R25, лента 15 мм',drum,'Drive','orange',mass=drum.Volume*2.7e-6)
    obj('OutputShaft'+side,f'02.6 / {side} выходной вал Ø12, отдельно от мотора',cyl(x,y,dz,6,39),'Drive')
    # Independent bearing carrier, two posts attach to base beyond motor OD.
    bz=dz+dw+9
    carrier=rounded(x-61,y-20,bz,122,40,6,6).cut(cyl(x,y,bz-1,14,8))
    obj('BearingBridge'+side,f'02.7 / {side} мост опоры барабана',carrier,'Drive')
    for sign in [-1,1]:
        obj('BearingPost'+side+str(sign).replace('-','M'),f'02.8 / {side} стойка моста',
            ring(x+sign*55,y,z+t+4,5,2.75,bz-z-t-4),'Drive')
    obj('Bearing'+side,f'02.9 / {side} опора Ø28/12, РЕЗЕРВ',ring(x,y,bz,14,6,6),'Drive','motor',kind='bearing envelope, selection pending')
    obj('OutputEncoder'+side,f'02.10 / {side} энкодер барабана, РЕЗЕРВ',box(x-12,y-12,bz+14,24,24,4),'Control','green')
    # Straight tangent webbing exits forward, dedicated force-sensing fairlead.
    tx=x+(-(rr+0.6) if side=='L' else (rr+0.6)); tz=dz+2+dw/2
    obj('Webbing'+side,f'03.1 / {side} тяговая лента → быстросброс → повод',box(tx-0.6,-218,tz-7.5,1.2,190,15),
        'Reins','blue','routing; winding and moving poses not modeled')
    guide=box(tx-12,-117,tz-14,24,18,28).cut(box(tx-3,-118,tz-9,6,20,18))
    obj('Fairlead'+side,f'03.2 / {side} направляющая с датчиком усилия, РЕЗЕРВ',guide,'Reins','metal',kind='sensor fairlead envelope; flexure/load path TBD')
    obj('FairleadStand'+side,f'03.3 / {side} стойка направляющей',box(tx-12,-117,z+t,24,18,tz-14-z-t),'Reins')
    release=rounded(tx-9,-208,tz-11,18,36,22,4).cut(box(tx-2,-209,tz-8,4,38,16))
    obj('ManualRelease'+side,f'03.4 / {side} размыкатель под нагрузкой, РЕЗЕРВ',release,'Reins','red',kind='manual loaded-release envelope, NOT designed hardware')

# Rear removable battery cassette; alternate location is saddle bag, not both.
bw,bd,bh=[P[k] for k in ['battery_width_mm','battery_depth_mm','battery_height_mm']]
bx,by,bz=-bw/2,57,z+t+4
tray=rounded(bx-4,by-4,z+t,bw+8,bd+8,12,5).cut(box(bx-1,by-1,z+t+3,bw+2,bd+2,11))
obj('BatteryTray','04.1 / Съёмный лоток батареи',tray,'Power','metal')
obj('Battery','04.2 / Батарея 180 × 70 × 65, РЕЗЕРВ',box(bx,by,bz,bw,bd,bh),'Power','green','pack envelope; electrical selection pending',1.15)
for x in [-55,55]:
    strap=box(x-9,by-3,bz+bh,18,bd+6,2).fuse(box(x-9,by-3,bz+9,18,2,bh-9)).fuse(box(x-9,by+bd+1,bz+9,18,2,bh-9))
    obj('BatteryStrap'+str(x).replace('-','M'),'04.3 / Удерживающая стропа батареи',strap,'Power','rubber')
obj('PowerConnector','04.4 / Разъём батареи, резерв доступа',box(95,80,82,19,27,19),'Power','orange')

# Electronics above battery, isolated by removable shelf and air gap.
obj('ElectronicsShelf','05.1 / Полка электроники',rounded(-110,51,149,220,83,3,6),'Control')
for x in [-103,103]:
    for y in [58,127]: obj('ShelfPost'+str(x).replace('-','M')+str(y),'05.2 / Стойка полки',ring(x,y,z+t,3.5,1.6,149-z-t),'Control')
obj('ESP32Board','05.3 / ESP32 + CAN + ADC, плата 78 × 45',box(-93,66,157,78,45,1.6),'Control','green',kind='PCB reservation, not a routed board')
obj('ESP32Antenna','05.4 / Зона антенны — без металла сверху',box(-92,66,159,18,18,5),'Control','orange',kind='antenna keepout reference')
obj('DCDC','05.5 / DC/DC питания логики, РЕЗЕРВ',box(-2,61,156,40,36,18),'Control','blue')
obj('SafetyRelay','05.6 / Аппаратная цепь разрешения, РЕЗЕРВ',box(49,60,156,42,34,30),'Control','orange')
obj('Fuse','05.7 / Предохранитель рядом с вводом, РЕЗЕРВ',box(45,105,156,38,16,17),'Control','red')
obj('EStopStem','05.8 / E-stop основание',cyl(125,103,184,11,32),'Control','orange')
obj('EStopHead','05.9 / E-stop фиксируемая кнопка',cyl(125,103,216,19,10),'Control','red')

# Rounded polymer shell with two webbing slots, no conductive cage over antenna.
shell=rounded(-w/2-3,-d/2-3,z+t,w+6,d+6,146,17)
shell=shell.cut(rounded(-w/2,-d/2,z+t-1,w,d,144,14))
for x in [-97,97]: shell=shell.cut(box(x-5,-d/2-5,160,10,12,38))
shell=shell.cut(cyl(125,103,205,11.5,20))
cover=obj('Guard','06.1 / Съёмный защитный кожух 3 мм — прозрачный вид',shell,'Cover','cover',mass=shell.Volume*1.2e-6)
if cover.ViewObject: cover.ViewObject.Transparency=82
clearance=obj('WithersClearance','07.1 / Контрольный просвет 140 мм — проверить по седлу',
    box(-70,-145,0,140,290,58),'Reference','red','reference only; not anatomical clearance approval',visible=False)

sheet=doc.addObject('Spreadsheet::Sheet','Parameters')
sheet.Label='00 / Параметры генератора (снимок, мм)'
sheet.set('A1','Изменения: layout-parameters.json → повторный запуск build-module.py')
for row,(key,val) in enumerate(P.items(),3):
    sheet.set('A'+str(row),key); sheet.set('B'+str(row),str(val))
sheet.setColumnWidth('A',430); sheet.setColumnWidth('B',140)
doc.recompute()

report={'revision':REV,'freecad_version':App.Version()[:3],'parts':[], 'critical_clearances_mm':{}}
for o,g,c,m,visible in items:
    valid=o.Shape.isValid() and not o.Shape.isNull() and o.Shape.Volume>0 and len(o.Shape.Solids)==1
    report['parts'].append({'name':o.Name,'valid':valid,'solids':len(o.Shape.Solids),'volume_mm3':round(o.Shape.Volume,3)})
    if not valid: raise RuntimeError('Invalid BRep: '+o.Name)
for a,b in [('MotorL','MotorR'),('Battery','MotorL'),('Battery','MotorR'),('Battery','ElectronicsShelf'),('Guard','DrumL'),('Guard','DrumR')]:
    aa,bb=doc.getObject(a).Shape,doc.getObject(b).Shape
    overlap=aa.common(bb).Volume
    if overlap>0.01: raise RuntimeError(f'Unexpected interference {a}/{b}: {overlap}')
    report['critical_clearances_mm'][a+'/'+b]=round(aa.distToShape(bb)[0],3)

solid_items=[o for o,g,c,m,visible in items if g!='Reference']
path=HERE/'cad/assembly'/('kulager-module-'+REV+'.FCStd')
if App.GuiUp:
    import FreeCADGui as Gui
    Gui.activeDocument().activeView().viewAxonometric()
    Gui.activeDocument().activeView().fitAll()
doc.saveAs(str(path))
Part.export(solid_items,str(HERE/'exports'/('kulager-module-'+REV+'.step')))
for name in ['Base','DrumL','MotorAdapterL','BatteryTray','Guard']:
    o=doc.getObject(name)
    target={'Base':'base','DrumL':'rein-drum','MotorAdapterL':'motor-adapter-blank','BatteryTray':'battery-tray','Guard':'guard'}[name]
    Part.export([o],str(HERE/'exports'/(target+'-'+REV+'.step')))
    if name in ['BatteryTray','Guard']:
        mesh=MeshPart.meshFromShape(Shape=o.Shape,LinearDeflection=0.25,AngularDeflection=0.25,Relative=False)
        mesh.write(str(HERE/'exports'/(target+'-'+REV+'.stl')))
with (HERE/'calculations'/('geometry-check-'+REV+'.json')).open('w') as f: json.dump(report,f,indent=2)
with (HERE/'calculations'/('cad-parts-'+REV+'.csv')).open('w') as f:
    writer=csv.writer(f); writer.writerow(['name','description','status','volume_mm3','estimated_mass_kg'])
    for o,g,c,m,visible in items: writer.writerow([o.Name,o.Label,o.Status,round(o.Shape.Volume,2),m if m is not None else 'TBD'])

# Tessellation for a reproducible drawing/preview without a graphical display.
scene=[]
for o,g,c,m,visible in items:
    if not visible or g=='Reference': continue
    verts,faces=o.Shape.tessellate(0.8)
    scene.append({'name':o.Name,'group':g,'color':colors[c],
                  'vertices':[[v.x,v.y,v.z] for v in verts], 'faces':faces})
(HERE/'exports'/('preview-mesh-'+REV+'.json')).write_text(json.dumps(scene,separators=(',',':')))
print('SUCCESS:',path)
print('Validated',len(items),'BRep objects;',len(report['critical_clearances_mm']),'clearance pairs')

"""Four FreeCAD mounting concepts on an assumed 17-inch English saddle.

Input dimensions are visual-design assumptions, not a saddle fit standard.
Run with FreeCAD Python; GUI mode is enabled by KULAGER_GUI=1.
Existing rA assembly is read only. Generated rB files are overwritten.
"""
import json
import os
from pathlib import Path
import FreeCAD as App
import Part

ROOT=Path(__file__).resolve().parents[3]
M=ROOT/'mechanical'
P=json.loads((M/'cad/scripts/saddle-templates-parameters.json').read_text())
S=P['saddle']; REV=P['revision']; V=App.Vector
if os.environ.get('KULAGER_GUI')=='1':
    import FreeCADGui as Gui
    Gui.showMainWindow()
for path in ['cad/assembly/saddle-templates','exports/saddle-templates','drawings/saddle-templates']:
    (M/path).mkdir(parents=True,exist_ok=True)
source=App.openDocument(str(M/'cad/assembly/kulager-module-rA-concept.FCStd'))
COL={'leather':(.28,.13,.07),'seat':(.40,.21,.12),'pad':(.73,.66,.52),
     'knee':(.34,.17,.095),'frame':(.12,.43,.66),'metal':(.68,.74,.80),
     'battery':(.16,.57,.37),'cable':(.12,.18,.22),'clearance':(.85,.22,.20),
     'guard':(.58,.76,.85),'board':(.12,.53,.37)}

def box(x,y,z,w,d,h): return Part.makeBox(w,d,h,V(x,y,z))
def cylinder(x,y,z,r,h): return Part.makeCylinder(r,h,V(x,y,z))
def rounded(x,y,z,w,d,h,r=8):
    shape=box(x+r,y,z,w-2*r,d,h).fuse(box(x,y+r,z,w,d-2*r,h))
    for xx in [x+r,x+w-r]:
        for yy in [y+r,y+d-r]: shape=shape.fuse(cylinder(xx,yy,z,r,h))
    return shape.removeSplitter()
def move(shape,delta):
    copy=shape.copy(); copy.translate(V(*delta)); return copy
def beam(a,b,r=9):
    a,b=V(*a),V(*b); v=b-a
    return Part.makeCylinder(r,v.Length,a,v)
def polywire(points): return Part.makePolygon([V(*p) for p in points+[points[0]]])
def cross_section(y,width,z,rise,thick):
    pts=[V(-width,y,z+rise),V(-width*.5,y,z+rise*.22),V(0,y,z),V(width*.5,y,z+rise*.22),V(width,y,z+rise)]
    top=Part.BSplineCurve(); top.interpolate(pts)
    bp=[p-V(0,0,thick) for p in reversed(pts)]
    bottom=Part.BSplineCurve(); bottom.interpolate(bp)
    return Part.Wire([top.toShape(),Part.makeLine(pts[-1],bp[0]),bottom.toShape(),Part.makeLine(bp[-1],pts[0])])
def loft_sections(stations,thick=14,ruled=False):
    return Part.makeLoft([cross_section(*st,thick) for st in stations],True,ruled)

def saddle_shapes():
    length=S['body_length_mm']; scale=length/470; ws=S['seat_max_width_mm']/300
    seat=loft_sections([(y*scale,w*ws,z,rise) for y,w,z,rise in
        [(0,65,142,8),(65,100,109,13),(180,137,91,12),(300,150,102,13),(420,142,150,12),(470,120,170,8)]])
    yield 'Seat','Сиденье: условный размер 17 дюймов',seat,'seat'
    pw=S['pad_width_mm']/2; end=S['pad_length_mm']-35
    pad=loft_sections([(-35,pw-20,30,-100),(100,pw,30,-120),(end-125,pw,30,-120),(end,pw-20,30,-100)],12,True)
    yield 'Pad','Вальтрап 540 × 460 — условная поверхность',pad,'pad'
    for side,sign in [('L',-1),('R',1)]:
        panel=rounded(sign*85-42,15,24,84,430,29,28)
        yield 'Panel'+side,'Подушка седла '+side,panel,'leather'
        # Smooth periodic flap outline in the YZ plane; thickness is schematic.
        pts=[V(sign*163,y*scale,z) for y,z in [(50,65),(115,89),(235,72),(332,4),(343,-145),
              (282,S['flap_bottom_z_mm']),(150,-235),(82,-120)]]
        c=Part.BSplineCurve(); c.interpolate(pts,PeriodicFlag=True)
        flap=Part.Face(Part.Wire([c.toShape()])).extrude(V(sign*10,0,0))
        yield 'Flap'+side,'Крыло седла '+side,flap,'leather'
        # Raised knee roll on each outer flap, ellipsoid used only as a visual reference.
        mat=App.Matrix(); mat.A11=12; mat.A22=42; mat.A33=113
        roll=Part.makeSphere(1).transformGeometry(mat); roll.translate(V(sign*178,108,-30))
        yield 'KneeRoll'+side,'Коленный упор '+side,roll,'knee'
        billet=box(sign*183-4,190,-230,8,24,215)
        yield 'Billet'+side,'Условная приструга '+side,billet,'seat'
    for name,label,y,w,z in [('Pommel','Передняя лука',12,108,151),('Cantle','Задняя лука',452,139,185)]:
        wires=[]
        for f in [-1,-.75,0,.75,1]:
            xx=f*w; zz=z-45*f*f
            wires.append(polywire([(xx,y-9,zz),(xx,y+10,zz),(xx,y+10,zz+19),(xx,y-9,zz+19)]))
        yield name,label,Part.makeLoft(wires,True,False),'seat'

class Assembly:
    def __init__(self,name,label):
        if name in App.listDocuments(): App.closeDocument(name)
        self.doc=App.newDocument(name); self.doc.Label=label; self.items=[]; self.groups={}
        for key,title in [('Saddle','01 / Условное седло 17 дюймов'),('Mount','02 / Рама и точки крепления'),
                          ('Module','03 / Моторы и механика'),('Power','04 / Батарея и электроника'),
                          ('Guards','05 / Кожухи — скрыть для обзора'),('Routes','06 / Маршруты тяг и кабелей'),
                          ('Reference','07 / Контрольные зоны — включить для проверки')]:
            g=self.doc.addObject('App::DocumentObjectGroup',key);g.Label=title;self.groups[key]=g
    def add(self,name,label,shape,group,color,role='concept',visible=True):
        if not shape.isValid() or shape.Volume<=0: raise RuntimeError('Invalid shape '+name)
        o=self.doc.addObject('PartDesign::Feature',name); o.Label=label; o.Shape=shape
        o.addProperty('App::PropertyString','DesignStatus','Template').DesignStatus=role
        o.addProperty('App::PropertyString','Revision','Template').Revision=REV
        self.groups[group].addObject(o)
        color=COL.get(color,color)
        if App.GuiUp:
            o.ViewObject.ShapeColor=color; o.ViewObject.LineColor=(.13,.15,.16)
            o.ViewObject.Visibility=visible
            if group=='Guards': o.ViewObject.Transparency=80
            if group=='Reference': o.ViewObject.Transparency=85
        self.items.append((o,group,color,visible)); return o
    def copy_source(self,name,delta,group=None):
        o=source.getObject(name)
        if group is None:
            group='Guards' if name=='Guard' else ('Power' if o in source.Power.Group or o in source.Control.Group else 'Module')
        color=tuple(o.ViewObject.ShapeColor[:3]) if App.GuiUp else (.55,.6,.65)
        return self.add(name,o.Label,move(o.Shape,delta),group,color,'rA envelope; interfaces pending')
    def add_saddle(self):
        for name,label,shape,color in saddle_shapes(): self.add(name,label,shape,'Saddle',color,'assumed saddle surface, not vendor CAD')
    def anchors(self):
        for sign,side in [(-1,'L'),(1,'R')]:
            x=sign*S['anchor_half_spacing_mm']
            for idx,y in enumerate([S['front_anchor_y_mm'],S['rear_anchor_y_mm']]):
                pad=rounded(x-22,y-25,S['anchor_z_mm']-12,44,50,6,6)
                plate=rounded(x-22,y-25,S['anchor_z_mm']-6,44,50,6,6)
                plate=plate.cut(cylinder(x,y,S['anchor_z_mm']-7,4.5,8))
                self.add('AnchorPad'+side+str(idx),'Прокладка под адаптер '+side+str(idx),pad,'Mount','cable','saddle-dependent interface')
                self.add('Anchor'+side+str(idx),'Адаптер к ленчику '+side+str(idx),plate,'Mount','frame','anchor positions are assumptions; no leather-only fastening')
            self.add('Rail'+side,'Продольная связь адаптеров '+side,beam((x,60,86),(x,225,86),8),'Mount','frame')
    def strut(self,name,a,b,r=8): return self.add(name,'Рама / '+name,beam(a,b,r),'Mount','frame','load path template; joints not engineered')
    def route(self,name,points,color='cable',radius=2):
        shapes=[beam(a,b,radius) for a,b in zip(points,points[1:])]
        return self.add(name,'Маршрут / '+name,Part.makeCompound(shapes),'Routes',color,'route only; clamp positions and bend radii pending')
    def references(self):
        self.add('RiderZone','Условный объём всадника, не антропометрия',box(-150,135,115,300,290,440),'Reference','clearance','fit review only',False)
        self.add('WithersZone','Условный объём холки, не анатомия',box(-70,-280,-10,140,310,140),'Reference','clearance','fit review only',False)
    def finish(self,filename):
        table=self.doc.addObject('Spreadsheet::Sheet','Assumptions'); table.Label='00 / Размеры и допущения'
        table.set('A1','Шаблон 17 дюймов. JSON → повторная генерация. Не стандарт посадки.')
        for row,(key,val) in enumerate(S.items(),3): table.set('A'+str(row),key);table.set('B'+str(row),str(val))
        table.setColumnWidth('A',420)
        self.doc.recompute()
        if App.GuiUp:
            Gui.activeDocument().activeView().viewAxonometric();Gui.activeDocument().activeView().fitAll()
        path=M/'cad/assembly/saddle-templates'/(filename+'.FCStd'); self.doc.saveAs(str(path))
        physical=[o for o,g,c,v in self.items if g not in ['Reference','Routes']]
        Part.export(physical,str(M/'exports/saddle-templates'/(filename+'.step')))
        mesh=[]
        for o,g,c,v in self.items:
            if not v: continue
            verts,faces=o.Shape.tessellate(1.2)
            mesh.append({'name':o.Name,'group':g,'color':c,'vertices':[[p.x,p.y,p.z] for p in verts],'faces':faces})
        (M/'exports/saddle-templates'/(filename+'-mesh.json')).write_text(json.dumps(mesh,separators=(',',':')))
        return path

def rear_battery(a,electronics=False):
    a.add('RearRack','Задняя полка батареи',rounded(-108,490,132,216,94,6),'Mount','frame')
    a.add('RearBattery','Батарея 180 × 70 × 65 за задней лукой',rounded(-90,502,138,180,70,65,5),'Power','battery','battery envelope, chemistry pending')
    for x in [-55,55]:
        strap=box(x-8,499,203,16,76,3).fuse(box(x-8,499,138,16,3,65)).fuse(box(x-8,572,138,16,3,65))
        a.add('BatteryStrap'+str(x).replace('-','M'),'Стропа батареи',strap,'Power','cable')
    for sign in [-1,1]:
        a.strut('RearStay'+str(sign).replace('-','M'),(sign*175,225,86),(sign*100,505,130))
        a.strut('RearBrace'+str(sign).replace('-','M'),(sign*175,60,86),(sign*100,563,130),6)
    if electronics:
        a.add('RearController','ESP32 / CAN / защита — резерв',rounded(-50,512,208,100,50,25,5),'Power','board')
    a.add('RearBatteryCover','Кожух задней батареи',rounded(-96,496,135,192,82,104 if electronics else 74,7).cut(
        rounded(-93,499,134,186,76,102 if electronics else 72,5)),'Guards','guard')

def copy_channels(a,side,delta):
    names=[o.Name for o in source.Drive.Group if o.Name.endswith(side) or o.Name.startswith('BearingPost'+side)]
    names += [o.Name for o in source.Reins.Group if o.Name.endswith(side)]
    names += ['OutputEncoder'+side]
    for name in names: a.copy_source(name,delta)

assemblies=[]; reports=[]
for number,cfg in enumerate(P['variants'],1):
    a=Assembly('SaddleVariant'+str(number),cfg['title']+' / '+REV)
    a.add_saddle();a.anchors();mode=cfg['mode']; dz=cfg['module_z_offset_mm']
    if mode in ['console','bridge']:
        dy=cfg['module_y_mm']; delta=(0,dy,dz)
        names=['Base']+[o.Name for group in ['Drive','Reins','Power','Control','Cover'] for o in source.getObject(group).Group]
        for name in names: a.copy_source(name,delta)
        for side,sign in [('L',-1),('R',1)]:
            x=sign*175; tx=sign*137
            a.strut('UpperStay'+side,(x,60,86),(tx,dy-80,56+dz))
            a.strut('RearStay'+side,(x,225,86),(tx,dy+102,56+dz))
            a.strut('Diagonal'+side,(x,225,86),(tx,dy-80,56+dz),6)
            a.add('DeckShoe'+side,'Съёмный ложемент модуля '+side,rounded(tx-15,dy-100,54+dz,30,225,6,5),'Mount','frame')
    elif mode=='pods':
        dy=cfg['motor_center_y_mm']+28
        for side,sign in [('L',-1),('R',1)]:
            x=sign*cfg['pod_half_spacing_mm']; dx=x-sign*72
            copy_channels(a,side,(dx,dy,dz))
            a.add('PodDeck'+side,'Полка бокового привода '+side,rounded(x-66,dy-125,60+dz,132,184,6,8),'Mount','frame')
            for yy in [60,225]: a.strut('PodStay'+side+str(yy),(sign*175,yy,86),(x,dy+(20 if yy==225 else -92),60+dz))
            a.add('PodGuard'+side,'Кожух бокового привода '+side,rounded(x-66,dy-128,66+dz,132,192,147,9).cut(
                rounded(x-63,dy-125,65+dz,126,186,145,7)).cut(box(x+sign*25.6-5,dy-132,160+dz,10,12,38)),
                'Guards','guard')
        rear_battery(a,True)
        for sign in [-1,1]: a.route('SideHarness'+str(sign).replace('-','M'),[(sign*235,100,170),(sign*192,200,65),(sign*192,460,80),(sign*90,520,200)])
        a.add('PodEStop','E-stop на правом боковом блоке',cylinder(235,95,252,10,10).fuse(cylinder(235,95,262,18,9)),
              'Power',(.85,.12,.13),'button envelope; circuit not implemented')
    else:
        dy=cfg['module_y_mm']; delta=(0,dy,dz)
        for side in ['L','R']: copy_channels(a,side,delta)
        deck=rounded(-160,dy-90,60+dz,320,170,6,12)
        for x in [-97.6,97.6]: deck=deck.fuse(rounded(x-16,dy-125,60+dz,32,40,6,5))
        a.add('CompactDeck','Передняя плита 320 × 170 с опорами направляющих',deck.removeSplitter(),'Mount','frame')
        a.add('FrontController','ESP32 / CAN в заднем кармане переднего блока',rounded(-48,dy+31,75+dz,96,42,27,5),'Power','board')
        shell=rounded(-163,dy-93,66+dz,326,176,146,14).cut(rounded(-160,dy-90,65+dz,320,170,144,11))
        for x in [-97.6,97.6]: shell=shell.cut(box(x-5,dy-96,160+dz,10,12,38))
        shell=shell.cut(cylinder(135,dy+51,207+dz,10.5,8))
        a.add('CompactGuard','Компактный передний кожух',shell,'Guards','guard')
        a.add('FrontEStop','E-stop на переднем блоке',cylinder(135,dy+51,198+dz,10,20).fuse(cylinder(135,dy+51,218+dz,18,9)),
              'Power',(.85,.12,.13),'button envelope; circuit not implemented')
        for side,sign in [('L',-1),('R',1)]:
            a.strut('FrontStay'+side,(sign*175,60,86),(sign*140,dy-65,57+dz))
            a.strut('FrontBrace'+side,(sign*175,225,86),(sign*140,dy+55,57+dz))
            a.add('CompactShoe'+side,'Ложемент переднего блока '+side,rounded(sign*140-12,dy-80,54+dz,24,150,6,5),'Mount','frame')
        rear_battery(a)
        a.route('PowerHarness',[(-90,520,165),(-193,460,80),(-193,225,65),(-193,60,90),(-130,dy+55,210)])
    a.references(); filename=cfg['id']+'-'+REV
    a.finish(filename); assemblies.append(a)
    # Interference of module/battery envelopes with saddle, not intended frame joints.
    saddle=[o for o,g,c,v in a.items if g=='Saddle']
    payload=[o for o,g,c,v in a.items if g in ['Module','Power','Guards']]
    overlaps=[]
    for u in payload:
        for v in saddle:
            if u.Shape.BoundBox.intersect(v.Shape.BoundBox):
                volume=u.Shape.common(v.Shape).Volume
                if volume>.1: overlaps.append({'module':u.Name,'saddle':v.Name,'volume_mm3':round(volume,2)})
    zone=a.doc.getObject('RiderZone').Shape
    intrusion=sum(o.Shape.common(zone).Volume for o in payload if o.Shape.BoundBox.intersect(zone.BoundBox))
    bounds=Part.makeCompound([o.Shape for o,g,c,v in a.items if g!='Reference']).BoundBox
    reports.append({'variant':cfg['id'],'objects':len(a.items),'all_brep_valid':all(o.Shape.isValid() for o,g,c,v in a.items),
        'saddle_payload_interferences':overlaps,'rider_reference_intrusion_mm3':round(intrusion,2),
        'overall_bounds_xyz_mm':[round(bounds.XLength,1),round(bounds.YLength,1),round(bounds.ZLength,1)]})
    print('Built',filename,flush=True)

# One file to compare all four assemblies at once. Individual documents retain origins.
comparison=App.newDocument('SaddleComparison');comparison.Label='KULAGER / 4 варианта установки / rB'
for i,a in enumerate(assemblies):
    group=comparison.addObject('App::DocumentObjectGroup','Variant'+str(i+1));group.Label=P['variants'][i]['title']
    delta=V((i%2)*1000,(i//2)*1300,0)
    for src,g,c,visible in a.items:
        dst=comparison.addObject('PartDesign::Feature','V'+str(i+1)+'_'+src.Name);dst.Label=src.Label
        shape=src.Shape.copy();shape.translate(delta);dst.Shape=shape;group.addObject(dst)
        if App.GuiUp:
            dst.ViewObject.ShapeColor=c;dst.ViewObject.Visibility=visible
            if g=='Guards': dst.ViewObject.Transparency=80
comparison.recompute()
if App.GuiUp:
    Gui.activeDocument().activeView().viewAxonometric();Gui.activeDocument().activeView().fitAll()
comparison.saveAs(str(M/'cad/assembly/saddle-templates'/('saddle-comparison-'+REV+'.FCStd')))
template=Assembly('SaddleOnly','Седло 17 дюймов / исходный шаблон')
template.add_saddle();template.finish('saddle-reference-17in-'+REV)
(M/'calculations'/('saddle-templates-check-'+REV+'.json')).write_text(json.dumps(reports,indent=2))
print(json.dumps(reports,indent=2),flush=True)

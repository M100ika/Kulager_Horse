"""Render actual FreeCAD tessellation to PNG and a dimensioned concept PDF.
Uses matplotlib, no FreeCAD required; run after build-module.py.
"""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

ROOT=Path(__file__).resolve().parents[3]
MECH=ROOT/'mechanical'
SCENE=json.loads((MECH/'exports/preview-mesh-rA-concept.json').read_text())
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})

def draw(ax,view='iso',cover=False):
    if view=='iso':
        direction=np.array([1,-1.6,1.35]); direction=direction/np.linalg.norm(direction)
        right=np.cross([0,0,1],direction); right=right/np.linalg.norm(right)
        up=np.cross(direction,right)
    else:
        right,up,direction={'top':([1,0,0],[0,-1,0],[0,0,1]),
                            'front':([1,0,0],[0,0,1],[0,-1,0]),
                            'side':([0,1,0],[0,0,1],[1,0,0])}[view]
        right,up,direction=map(np.array,(right,up,direction))
    polygons=[]
    for item in SCENE:
        if item['name']=='Guard' and not cover: continue
        verts=np.array(item['vertices']); faces=np.array(item['faces'])
        for face in faces:
            tri=verts[face]; normal=np.cross(tri[1]-tri[0],tri[2]-tri[0]); length=np.linalg.norm(normal)
            if length<1e-8: continue
            normal/=length
            shade=0.67+0.33*abs(np.dot(normal,np.array([.3,-.5,.812])))
            col=np.clip(np.array(item['color'])*shade,0,1)
            xy=np.column_stack((tri@right,tri@up))
            polygons.append((tri@direction,xy,col))
    # Pixel depth buffer: centre-depth sorting is incorrect for large CAD faces.
    allxy=np.concatenate([v[1] for v in polygons]); lo=allxy.min(axis=0)-10; hi=allxy.max(axis=0)+10
    scale=1300/max(hi-lo); nx,ny=np.ceil((hi-lo)*scale).astype(int)
    pixels=np.ones((ny,nx,4)); pixels[:,:,3]=0
    zbuffer=np.full((ny,nx),-np.inf)
    for depth,xy,col in polygons:
        q=(xy-lo)*scale
        xmin,ymin=np.maximum(np.floor(q.min(axis=0)).astype(int),0)
        xmax,ymax=np.minimum(np.ceil(q.max(axis=0)).astype(int),[nx-1,ny-1])
        a,b,c=q; denominator=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(denominator)<1e-9: continue
        xx,yy=np.meshgrid(np.arange(xmin,xmax+1)+.5,np.arange(ymin,ymax+1)+.5)
        wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denominator
        wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denominator
        wc=1-wa-wb; zz=wa*depth[0]+wb*depth[1]+wc*depth[2]
        region=zbuffer[ymin:ymax+1,xmin:xmax+1]
        mask=(wa>=-1e-6)&(wb>=-1e-6)&(wc>=-1e-6)&(zz>=region)
        region[mask]=zz[mask]
        pixels[ymin:ymax+1,xmin:xmax+1][mask]=[*col,1]
    ax.imshow(pixels,origin='lower',extent=[lo[0],hi[0],lo[1],hi[1]],interpolation='bilinear')
    ax.set_xlim(lo[0]-35,hi[0]+35); ax.set_ylim(lo[1]-35,hi[1]+35)
    ax.set_aspect('equal'); ax.axis('off')
    return lambda p: (np.dot(p,right),np.dot(p,up))

def label(ax,project,text,point,offset):
    xy=project(point)
    ax.annotate(text,xy=xy,xytext=offset,textcoords='offset points',fontsize=9,
                bbox={'boxstyle':'round,pad=.4','fc':'white','ec':'#cbd5e1','alpha':.96},
                arrowprops={'arrowstyle':'-','color':'#64748b','lw':.9})

fig=plt.figure(figsize=(15,10),facecolor='#f3f6f9')
ax=fig.add_axes([.08,.12,.79,.76]); p=draw(ax)
label(ax,p,'AK80-64 × 2\nØ98 × 61.9 мм',(-72,-28,104),(-180,35))
label(ax,p,'Размыкаемая муфта\n+ ограничитель (резерв)',(72,-28,145),(80,10))
label(ax,p,'Барабаны R25\nс независимой опорой',(-72,-28,173),(-175,95))
label(ax,p,'Батарея\n180 × 70 × 65 мм',(0,92,102),(160,-130))
label(ax,p,'ESP32 / CAN / защита',(0,90,172),(70,70))
label(ax,p,'Регулируемые адаптеры\nк силовым точкам седла',(-115,-87,28),(-180,-75))
label(ax,p,'Ручной сброс под нагрузкой\n(посадочный резерв)',(97,-185,171),(60,-75))
fig.text(.055,.94,'KULAGER / МОДУЛЬ ПОВОДЬЕВ',fontsize=24,weight='bold',color='#153747')
fig.text(.055,.903,'rA • Компоновка FreeCAD • Вид без кожуха • Размеры в миллиметрах',fontsize=12,color='#527080')
fig.text(.055,.046,'КОНЦЕПТ ДЛЯ СТЕНДА  /  Муфты, размыкатели, датчики и крепление требуют деталировки и проверки.',fontsize=11,color='#a63a25')
fig.savefig(MECH/'drawings/kulager-overview-rA-concept.png',dpi=170)
with PdfPages(MECH/'drawings/kulager-layout-rA-concept.pdf') as pdf:
    pdf.savefig(fig)
    fig2,axs=plt.subplots(2,2,figsize=(16.54,11.69))
    fig2.suptitle('KULAGER / Габаритная компоновка rA — не производственный чертёж',fontsize=19)
    for ax,view,title in [(axs[0,0],'top','ВИД СВЕРХУ / кожух снят'),(axs[0,1],'front','СПЕРЕДИ / кожух снят'),(axs[1,0],'side','СПРАВА / кожух снят'),(axs[1,1],'iso','ЗАКРЫТЫЙ КОЖУХ')]:
        draw(ax,view,view=='iso'); ax.set_title(title,fontsize=12)
        if view=='top':
            ax.annotate('',(-160,-156),(160,-156),arrowprops={'arrowstyle':'<->'})
            ax.text(0,-170,'320 — несущая плита',ha='center')
            ax.annotate('',(180,-140),(180,140),arrowprops={'arrowstyle':'<->'})
            ax.text(188,0,'280',rotation=90,va='center')
        if view=='front':
            ax.annotate('',(-115,-7),(115,-7),arrowprops={'arrowstyle':'<->'})
            ax.text(0,-22,'230 ном. / настройка 200…260*',ha='center')
        if view=='side':
            ax.annotate('',(167,8),(167,226),arrowprops={'arrowstyle':'<->'})
            ax.text(175,117,'218',rotation=90,va='center')
    fig2.text(.06,.025,'Кожух: 326 × 286 мм. Высота до кнопки от опор: 218 мм. Ленты выходят вперёд (−Y).\n*Регулировка требует повторной генерации модели. Просвет и точки крепления проверяются на конкретном седле.',fontsize=11)
    fig2.subplots_adjust(bottom=.10,top=.91,wspace=.18,hspace=.22); pdf.savefig(fig2)
plt.close('all')
print('Rendered PNG and two-page concept PDF')

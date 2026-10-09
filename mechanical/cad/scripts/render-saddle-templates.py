"""Render actual FreeCAD meshes: four comparison images and a five-page PDF."""
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

M=Path(__file__).resolve().parents[3]/'mechanical'
P=json.loads((M/'cad/scripts/saddle-templates-parameters.json').read_text())
OUT=M/'drawings/saddle-templates'; OUT.mkdir(exist_ok=True,parents=True)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
NOTES=[
    ('Весь модуль перед седлом','Простая установка целого блока; большой вынос массы вперёд.'),
    ('Модуль над передней лукой','Короткая рама; высокий блок ограничивает место перед всадником.'),
    ('Левый и правый приводы по бокам','Центр открыт; проверить ширину и движения коленей.'),
    ('Приводы спереди, батарея сзади','Основной кандидат для макета; требуется защищённый жгут.')]

def render(scene,view):
    if view=='iso':
        direction=np.array([1.15,-1.7,1.25]);direction/=np.linalg.norm(direction)
        right=np.cross([0,0,1],direction);right/=np.linalg.norm(right);up=np.cross(direction,right)
    else:
        right,up,direction=map(np.array,{'side':([0,1,0],[0,0,1],[1,0,0]),'top':([1,0,0],[0,-1,0],[0,0,1])}[view])
    triangles=[]
    for item in scene:
        if item['group'] in ['Guards','Reference']: continue
        verts=np.array(item['vertices'])
        for face in item['faces']:
            tri=verts[list(face)];normal=np.cross(tri[1]-tri[0],tri[2]-tri[0]);n=np.linalg.norm(normal)
            if n<1e-9:continue
            normal/=n
            shade=.68+.32*abs(np.dot(normal,[.3,-.5,.812]))
            triangles.append((tri@direction,np.column_stack((tri@right,tri@up)),np.clip(np.array(item['color'])*shade,0,1)))
    # Common world envelope keeps all four options at the same scale.
    bound=np.column_stack((GLOBAL_VERTS@right,GLOBAL_VERTS@up));lo=bound.min(axis=0)-35;hi=bound.max(axis=0)+35
    scale=1150/max(hi-lo);nx,ny=np.ceil((hi-lo)*scale).astype(int)
    rgba=np.ones((ny,nx,4));rgba[:,:,3]=0;zbuf=np.full((ny,nx),-np.inf)
    for depth,xy,color in triangles:
        q=(xy-lo)*scale
        xmin,ymin=np.maximum(np.floor(q.min(axis=0)).astype(int),0)
        xmax,ymax=np.minimum(np.ceil(q.max(axis=0)).astype(int),[nx-1,ny-1])
        if xmin>xmax or ymin>ymax: continue
        a,b,c=q;den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(den)<1e-9:continue
        xx,yy=np.meshgrid(np.arange(xmin,xmax+1)+.5,np.arange(ymin,ymax+1)+.5)
        wa=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
        wb=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den;wc=1-wa-wb
        zz=wa*depth[0]+wb*depth[1]+wc*depth[2]
        region=zbuf[ymin:ymax+1,xmin:xmax+1]
        mask=(wa>=-1e-6)&(wb>=-1e-6)&(wc>=-1e-6)&(zz>=region)
        region[mask]=zz[mask];rgba[ymin:ymax+1,xmin:xmax+1][mask]=[*color,1]
    return rgba,lo,hi,right,up

def draw(ax,result):
    rgba,lo,hi,right,up=result
    ax.imshow(rgba,origin='lower',extent=[lo[0],hi[0],lo[1],hi[1]],interpolation='bilinear')
    ax.set_aspect('equal');ax.axis('off')
    return lambda xyz:(np.dot(xyz,right),np.dot(xyz,up))

SCENES=[json.loads((M/'exports/saddle-templates'/(cfg['id']+'-'+P['revision']+'-mesh.json')).read_text()) for cfg in P['variants']]
GLOBAL_VERTS=np.concatenate([np.array(item['vertices']) for scene in SCENES for item in scene if item['group'] not in ['Guards','Reference']])
results=[]
for cfg,scene in zip(P['variants'],SCENES):
    result={view:render(scene,view) for view in ['iso','side','top']}
    results.append(result);print('Rendered',cfg['id'],flush=True)

fig,axs=plt.subplots(2,2,figsize=(18,14),facecolor='#f3f6f8')
fig.text(.045,.954,'KULAGER / 4 ВАРИАНТА УСТАНОВКИ',fontsize=26,weight='bold',color='#163d50')
fig.text(.045,.926,'Одно условное седло 17″ • одинаковый масштаб • кожухи скрыты, механика показана',fontsize=13,color='#527080')
for i,(ax,cfg,res) in enumerate(zip(axs.flat,P['variants'],results)):
    draw(ax,res['iso']);ax.set_title(cfg['title'],loc='left',fontsize=17,color='#174f70',pad=6)
    ax.text(.02,.04,NOTES[i][0],transform=ax.transAxes,fontsize=12,weight='bold',color='#193b4c')
    ax.text(.02,-.01,NOTES[i][1],transform=ax.transAxes,fontsize=10,color='#4c5f6b')
fig.text(.045,.039,'Коричневое — седло; синее — новая рама; чёрное/оранжевое — приводы; зелёное — батарея и электроника.',fontsize=12)
fig.text(.045,.017,'rB / ШАБЛОНЫ ДЛЯ ОБСУЖДЕНИЯ. Размеры и силовые точки седла приняты условно.',fontsize=11,color='#9d4432')
fig.subplots_adjust(left=.045,right=.97,top=.878,bottom=.095,wspace=.05,hspace=.18)
fig.savefig(OUT/'saddle-options-rB-templates.png',dpi=160)
with PdfPages(OUT/'saddle-options-rB-templates.pdf') as pdf:
    pdf.savefig(fig)
    for i,(cfg,res) in enumerate(zip(P['variants'],results)):
        page=plt.figure(figsize=(16.54,11.69),facecolor='white')
        page.text(.045,.951,cfg['title'].upper(),fontsize=24,weight='bold',color='#163d50')
        page.text(.045,.916,NOTES[i][1],fontsize=13,color='#527080')
        iso=page.add_axes([.02,.20,.60,.67]);draw(iso,res['iso'])
        iso.text(.05,.96,'ОБЩИЙ ВИД / КОЖУХИ СКРЫТЫ',transform=iso.transAxes,color='#527080',fontsize=11)
        side=page.add_axes([.61,.48,.36,.36]);draw(side,res['side'])
        side.text(.02,.95,'ВИД СБОКУ',transform=side.transAxes,color='#527080',fontsize=11)
        side.annotate('к голове',xy=(-320,-170),xytext=(-90,-170),arrowprops={'arrowstyle':'->','color':'#527080'},color='#527080',fontsize=10)
        top=page.add_axes([.61,.12,.36,.36]);draw(top,res['top'])
        top.text(.02,.95,'ВИД СВЕРХУ',transform=top.transAxes,color='#527080',fontsize=11)
        # Mounting markers are plotted in projected coordinates, where top Y = -world Y.
        top.plot([-175,-175,175,175],[-60,-225,-60,-225],'o',color='#d84c2f',markersize=4)
        top.text(0,-610,'Красные точки: условные опоры рамы',ha='center',fontsize=8,color='#9d4432')
        page.text(.05,.12,'База для шаблонов: седло 17″ / 43 см.\nНаружная длина модели — 470 мм; ширина сиденья — 300 мм.\nОпоры: поперечная база 350 мм; продольная база 165 мм.\nБатарея во всех вариантах: резерв 180 × 70 × 65 мм.',fontsize=12,linespacing=1.7)
        page.text(.05,.035,'FreeCAD + STEP: '+cfg['id']+'-rB-templates\nКрепление к ленчику и свободное пространство всадника требуют проверки по реальному седлу.',fontsize=10,color='#9d4432')
        page.savefig(OUT/(cfg['id']+'-rB-templates.png'),dpi=150);pdf.savefig(page);plt.close(page)
plt.close(fig)
print('Saved four detail sheets, overview PNG and 5-page PDF',flush=True)

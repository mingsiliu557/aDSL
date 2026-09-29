from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
O=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/core_showcase_20260929');P=O/'panels';F=O/'figures';F.mkdir(exist_ok=True)
FONT='/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'
W=2400;H=1600;INK='#202C3C';MUTED='#5F6B7B';BLUE='#3576BF';ORANGE='#C98035';BG='#F4F6F9';BORDER='#DAE1E9'
def font(s):return ImageFont.truetype(FONT,s)
def text(im,xy,t,s=32,c=INK):
 d=ImageDraw.Draw(im);x,y=xy;latin=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',s);chinese=font(s)
 runs=[]
 for ch in t:
  which=chinese if ord(ch)>=0x2e80 else latin
  if runs and runs[-1][0] is which:runs[-1]=(which,runs[-1][1]+ch)
  else:runs.append((which,ch))
 for f,run in runs:
  d.text((x,y),run,font=f,fill=c,anchor='lt');x+=d.textlength(run,font=f)

def base(n,title,subtitle):
 im=Image.new('RGB',(W,H),'white');d=ImageDraw.Draw(im);d.rectangle((0,0,W,12),fill=BLUE)
 text(im,(65,45),n,27,BLUE);text(im,(65,92),title,60);text(im,(65,182),subtitle,30,MUTED);return im

def card(im,box,title,sub=None):
 d=ImageDraw.Draw(im);d.rounded_rectangle(box,radius=22,fill=BG,outline=BORDER,width=2);text(im,(box[0]+30,box[1]+22),title,40)
 if sub:text(im,(box[0]+30,box[1]+82),sub,27,MUTED)
def panel(im,name,box):
 a=Image.open(P/(name+'.png')).convert('RGBA');a.thumbnail((box[2]-box[0],box[3]-box[1]),Image.Resampling.LANCZOS);im.paste(a,(box[0]+(box[2]-box[0]-a.width)//2,box[1]+(box[3]-box[1]-a.height)//2),a)
def foot(im,lines):
 for i,line in enumerate(lines):text(im,(65,1460+46*i),line,26,MUTED)
def save(im,name):im.save(F/(name+'.png'));return im
pages=[]
# Shape API: same meshes, neutral display copies only.
im=base('01A / SHAPE EXPRESSION','SF21｜灯罩轮廓与真实开口','同一文本输入、同一模型与预算；左为增强前 API，右为新增构造 API + 建模提示。')
for i,a in enumerate(['A','B']):
 x=65+i*1170;card(im,(x,260,x+1100,1370),'A  增强前' if a=='A' else 'B  增强版','整体轮廓                                  灯罩局部放大（同类视角）')
 panel(im,'SF21_'+a,(x+5,430,x+360,1110));panel(im,'SF21_'+a+'_detail',(x+320,390,x+1090,1160))
 text(im,(x+40,1210),'圆柱分层近似；顶端被封住' if a=='A' else '旋转剖面；连续壳体与开口',34)
 text(im,(x+40,1275),'脚架比例与其他细节仍需单独判断',27,MUTED)
foot(im,['仅比较形状表达；未运行正式评审或物理 checker。模型按主体尺寸归一化展示，非同一毫米尺度。','来源：geometry_expression_20260929/{A,B}/SF21；未修改生成几何、未追加模型调用。'])
pages.append(save(im,'01a_SF21_shape_comparison'))
im=base('01B / SHAPE EXPRESSION','SF06｜扶手椅的软包轮廓与过渡','增强前与增强版各一次初始生成；展示副本隐藏原背景板，统一中性材质和取景。')
for i,a in enumerate(['A','B']):
 x=65+i*1170;card(im,(x,260,x+1100,1370),'A  增强前' if a=='A' else 'B  增强版')
 panel(im,'SF06_'+a,(x+10,350,x+1090,1210));text(im,(x+40,1260),'基础体组合：较明显的软包拼接' if a=='A' else '局部凸包过渡：轮廓更连贯',34)
foot(im,['这是 checker 关闭的形状展示案例，不是后续 SF06 完整流程的批准结果。','颜色、背景和取景仅用于展示；主体网格保持原样，不能据此宣称整体质量全面提高。'])
pages.append(save(im,'01b_SF06_shape_comparison'))
# SF13 grouping and exact connection counts.
im=base('02 / PRINT PARTS + MULTIPLE INTERFACES','SF13｜8 个打印件，24 处独立连接','两块侧板 + 五层层板 + 顶盖；每层横板左右各两处榫槽接口。颜色表示打印件。')
card(im,(65,260,795,1390),'装配状态','保持实际装配位置')
panel(im,'SF13_assembled',(60,390,800,1290))
card(im,(825,260,1615,1390),'分件与接口展示','人为拉开显示；红点与细线标记接口')
panel(im,'SF13_exploded',(820,390,1620,1300))
card(im,(1645,260,2335,1390),'接口清单','每个圆点对应一条实际 connection ID')
d=ImageDraw.Draw(im);m=json.loads((O/'resources/SF13/assembly_manifest.json').read_text());palette=json.loads((O/'render_inputs.json').read_text())['SF13']['colors']
rows=[('cap_top','顶盖'),('shelf_level_5','第 5 层'),('shelf_level_4','第 4 层'),('shelf_level_3','第 3 层'),('shelf_level_2','第 2 层'),('shelf_bottom','第 1 层')]
for j,label in enumerate(['左前','左后','右前','右后']):text(im,(1840+115*j,435),label,26,MUTED)
for i,(pid,label) in enumerate(rows):
 y=530+i*95;d.rounded_rectangle((1680,y+5,1710,y+35),radius=5,fill=palette[pid]);text(im,(1730,y),label,29)
 assert sum(c['tab_part']==pid for c in m['connections'])==4
 for j in range(4):d.ellipse((1870+j*115,y+6,1893+j*115,y+29),fill='#C64E46')
text(im,(1680,1180),'6 × 4 = 24 处接口',39,BLUE);text(im,(1680,1260),'历史 Topology：8 件 / 24 接口通过',23,MUTED)
foot(im,['使用微裂缝处理后的既有网格；这次只制作展示图，没有重新生成、修补或运行 checker。','爆炸距离、红点与连线仅为解释分组/连接，不代表真实装配插入路径；每对部件可有多个接口。'])
pages.append(save(im,'02_SF13_parts_and_interfaces'))
# SF16 central YZ section from actual solids.
sections=json.loads((O/'sections.json').read_text());D=json.loads((O/'render_inputs.json').read_text())
im=base('03 / GLOBAL MATERIAL INTERFERENCE','SF16｜斜背板与顶层板：互穿修复前后','同一输入的原始版本与 Agent 接受版本；蓝色为顶层板，橙色为斜背板，红色为实际交集。')
for i,key in enumerate(['SF16_before','SF16_after']):
 x=65+i*1170;card(im,(x,260,x+1100,1390),'修复前  ·  互穿 FAIL' if i==0 else 'Agent 修复后  ·  目标部件对 PASS')
 panel(im,key+'_assembled',(x,360,x+450,1060));text(im,(x+495,385),'局部剖面：装配坐标 x = 0 mm',27,MUTED)
 box=(x+485,460,x+1050,1040);d=ImageDraw.Draw(im);d.rectangle(box,fill='white',outline=BORDER,width=2)
 ymin,ymax=120,240;zmin,zmax=1590,1710
 def proj(p):return (box[0]+(p[0]-ymin)/(ymax-ymin)*(box[2]-box[0]),box[3]-(p[1]-zmin)/(zmax-zmin)*(box[3]-box[1]))
 # Clip polygons to viewport, preserving actual section shape.
 from shapely.geometry import Polygon,box as clipbox
 for name,color in [('top_shelf','#6898D2'),('slanted_back_panel','#DBA565'),('overlap','#D43F42')]:
  for poly in sections[key]['polygons'][name]:
   q=Polygon(poly).intersection(clipbox(ymin,zmin,ymax,zmax))
   if q.is_empty:continue
   for s in ([q] if q.geom_type=='Polygon' else q.geoms):
    pts=[proj(p) for p in s.exterior.coords];d.polygon(pts,fill=color);d.line(pts,fill='#42566A',width=2)
 for v in [120,160,200,240]:text(im,(proj((v,zmin))[0]-18,1055),str(v),21,MUTED)
 text(im,(x+760,1100),'y / mm',23,MUTED);text(im,(x+400,650),'z / mm',23,MUTED)
 for v in [1600,1650,1700]:text(im,(x+418,proj((ymin,v))[1]-14),str(v),19,MUTED)
 pair=D[key]['target_pair'];value=pair['undeclared_interference_mm3'];text(im,(x+40,1190),f'交集体积  {value:,.0f} mm³',42,'#C74440' if i==0 else '#2D8A66')
 text(im,(x+40,1270),f'数值容差  {pair["volume_tolerance_mm3"]:,.1f} mm³',28,MUTED)
foot(im,['局部图是实际最终实体的剖面与求交结果，不是用包围盒代替穿插；两图使用相同剖切位置和坐标范围。','选中部件对：slanted_back_panel / top_shelf。此例验证互穿检测和修复，不宣称已通过重力或装配路径验证。'])
pages.append(save(im,'03_SF16_interference_before_after'))
# SF10 grouping comparison.
im=base('04 / AGENT PARTITION OPTIMIZATION','SF10｜5 件 → 4 件：实际 Agent 分组修改','合并左支座与下横梁，撤销组内接口；同一主体参考，原装配外形保持不变。')
for i,key in enumerate(['SF10_before','SF10_after']):
 x=65+i*1170;card(im,(x,260,x+1100,1360),'基线  5 件 / 8 接口' if i==0 else '采用的候选  4 件 / 7 接口','上：装配状态       下：分件展示（颜色表示实际打印组）')
 panel(im,key+'_assembled',(x+250,370,x+850,730));panel(im,key+'_exploded',(x,650,x+1100,1190))
 text(im,(x+40,1205),'G = 0        O = 380.092859' if i==0 else 'G = 38      O = 381.337786',37,BLUE)
 text(im,(x+40,1280),'下横梁独立打印（粉色）' if i==0 else '下横梁并入左支座（同为橙色）',29)
foot(im,['同一参考：V = 616，h = 75 mm；Dapper 目标分数提高约 0.328%，不等于实际耗材或打印时间的改善。','两版 Topology / Overhang / Standing 均 PASS；候选分组被采用，但表面 HIGH 未解决，最终 approved 仍为 false。'])
pages.append(save(im,'04_SF10_five_to_four_parts'))
pages[0].save(F/'core_showcase.pdf',save_all=True,append_images=pages[1:],resolution=180)
# Easy skim contact sheet.
over=Image.new('RGB',(1500,1660),'#E9EEF4')
for i,p in enumerate(pages):
 a=p.copy();a.thumbnail((735,510));over.paste(a,(15+(i%2)*750,15+(i//2)*550))
over.save(F/'overview.jpg',quality=93)
print('Wrote five presentation figures, PDF and overview')

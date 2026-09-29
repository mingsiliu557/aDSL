from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
root=Path(__file__).parent
out=root/'comparisons';out.mkdir(exist_ok=True)
try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',17)
except OSError:font=ImageFont.load_default()
for case in ('SF06','SF21','T02-bookshelf'):
    if not all((root/a/case/'demo_result.json').is_file() for a in ('A','B')):continue
    results={a:json.loads((root/a/case/'demo_result.json').read_text()) for a in ('A','B')}
    for name,views in (('selected',[1,2,3,7]),('all',list(range(1,9)))):
        rows=2 if name=='selected' else 4
        image=Image.new('RGB',(1600,rows*430),(245,245,245));draw=ImageDraw.Draw(image)
        for ai,arm in enumerate(('A','B')):
            for j,view in enumerate(views):
                x=(j%4)*400;y=(ai*(len(views)//4)+j//4)*430
                p=root/arm/case/'views'/f'render_{view:04}.png'
                if p.is_file():image.paste(Image.open(p).convert('RGB').resize((400,400)),(x,y+30))
                draw.text((x+8,y+5),f'{case} {arm} - view {view} ({results[arm]["status"]})',fill='black',font=font)
        image.save(out/f'{case}_{name}.jpg',quality=92)

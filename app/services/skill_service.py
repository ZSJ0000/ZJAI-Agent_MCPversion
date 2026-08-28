import glob,os
from app.config import SKILLS_DIR
def load_skills():
 r=[]
 for p in glob.glob(os.path.join(SKILLS_DIR,"*.md")):
  with open(p,encoding="utf-8") as f:r.append({"id":os.path.basename(p),"name":os.path.basename(p)[:-3],"content":f.read()})
 return r

import os
BASE_DIR=os.path.abspath(os.path.join(os.path.dirname(__file__),".."))
CONFIG_DIR=os.path.join(BASE_DIR,"config"); DATA_DIR=os.path.join(BASE_DIR,"data"); SKILLS_DIR=os.path.join(BASE_DIR,"skills")
SECRET_KEY=os.environ.get("ZJAI_SECRET_KEY","change-this-secret-key")

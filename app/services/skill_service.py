import glob
import logging
import os

from app.config import SKILLS_DIR

log = logging.getLogger(__name__)


def load_skills():
    """加载 skills/*.md。

    单个文件读取失败不应让整个技能列表变空，因此逐文件容错；
    结果按文件名排序，保证多次调用顺序稳定。
    """
    skills = []
    for path in sorted(glob.glob(os.path.join(SKILLS_DIR, '*.md'))):
        try:
            with open(path, encoding='utf-8') as f:
                content = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            log.warning('跳过无法读取的 skill 文件 %s: %s', path, exc)
            continue
        name = os.path.basename(path)[:-3]
        skills.append({'id': os.path.basename(path), 'name': name, 'content': content})
    return skills

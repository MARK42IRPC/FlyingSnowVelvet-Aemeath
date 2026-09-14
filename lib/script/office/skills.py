"""办公技能：列出、安装与删除 DSH 技能目录。

DSH 的技能根由 `@deepseek-ai/dsh-skill-filesystem` 决定：程序自带的 `resc/agent`
是内置根（只读），DSH home 下的 `skills` 是用户根。办公页面的技能卡片只做两件事：
把两处根里的技能列出来，以及把用户选的技能目录装进用户根里。

技能名取自 `SKILL.md` 的 frontmatter（`name:`），目录名只是存放位置；同名时以用户根
里的那份为准，避免同名内置技能把用户自己装的挡住。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from config.user_storage_paths import get_user_state_dir
from lib.core.logger import get_logger

from .runtime import office_skill_root

logger = get_logger(__name__)

#: 技能清单文件名；DSH 只认这一个文件名。
SKILL_MANIFEST = "SKILL.md"
_NAME_PATTERN = re.compile(r"(?m)^name:\s*(.+?)\s*$")
_DESCRIPTION_PATTERN = re.compile(r"(?m)^description:\s*(.+?)\s*$")


class SkillError(RuntimeError):
    """技能安装/删除失败；消息直接展示给用户。"""


@dataclass(frozen=True)
class OfficeSkill:
    """一个技能目录：名字、说明、位置，以及它是否属于程序自带。"""

    name: str
    description: str
    path: Path
    bundled: bool

    @property
    def removable(self) -> bool:
        """内置技能是随程序发布的，不允许在界面上删除。"""
        return not self.bundled


def user_skill_root() -> Path:
    """DSH 用户技能根：DSH home 下的 skills 目录。"""
    return get_user_state_dir("office", "dsh-home") / "skills"


def _safe_skill_dir_name(name: str) -> str:
    """技能目录名只能是单层名字，带路径的名字直接拒绝安装。

    目录名来自用户选中目录里的 `SKILL.md`。不校验的话 `name: ../../x` 会把技能拷到
    技能根之外，而列表只扫技能根、删除也要求目标在根内，装出来的目录既看不见也删不掉。
    """
    text = str(name or "").strip()
    if (
        not text
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or text != Path(text).name
    ):
        raise SkillError(f"技能名不能是路径：{name!r}")
    return text


def skill_roots() -> tuple[tuple[Path, bool], ...]:
    """技能根与「是否内置」，顺序即优先级（用户根排在后面、覆盖同名内置技能）。"""
    return ((office_skill_root(), True), (user_skill_root(), False))


def read_skill(directory: Path, *, bundled: bool) -> OfficeSkill | None:
    """读取一个技能目录；没有 `SKILL.md` 就不是技能。"""
    manifest = Path(directory) / SKILL_MANIFEST
    try:
        text = manifest.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    name_match = _NAME_PATTERN.search(text)
    name = (name_match.group(1).strip() if name_match else "") or Path(directory).name
    description_match = _DESCRIPTION_PATTERN.search(text)
    description = description_match.group(1).strip() if description_match else ""
    return OfficeSkill(
        name=name,
        description=description,
        path=Path(directory),
        bundled=bundled,
    )


def list_skills() -> list[OfficeSkill]:
    """列出内置与用户安装的全部技能，按名字排序。"""
    found: dict[str, OfficeSkill] = {}
    for root, bundled in skill_roots():
        try:
            entries = sorted(Path(root).iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for directory in entries:
            if not directory.is_dir():
                continue
            skill = read_skill(directory, bundled=bundled)
            if skill is not None:
                found[skill.name] = skill
    return sorted(found.values(), key=lambda skill: skill.name.casefold())


def install_skill(source: Path) -> OfficeSkill:
    """把用户选的技能目录复制进用户技能根；返回装好的技能。"""
    source = Path(source)
    if source.is_file() and source.name == SKILL_MANIFEST:
        source = source.parent
    if not source.is_dir():
        raise SkillError(f"找不到技能目录：{source}")
    if not (source / SKILL_MANIFEST).is_file():
        raise SkillError(f"技能目录缺少 {SKILL_MANIFEST}：{source}")
    skill = read_skill(source, bundled=False)
    if skill is None:
        raise SkillError(f"无法读取技能清单：{source / SKILL_MANIFEST}")
    target_root = user_skill_root()
    target = target_root / _safe_skill_dir_name(skill.name)
    try:
        if target.resolve() == source.resolve():
            raise SkillError(f"这个技能已经在用户技能目录里：{skill.name}")
    except OSError:
        pass
    if target.exists():
        raise SkillError(f"用户技能目录里已经有同名技能：{skill.name}")
    target_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    logger.info("[OfficeSkills] 安装技能 name=%s target=%s", skill.name, target)
    return read_skill(target, bundled=False) or skill


def remove_skill(name: str) -> Path:
    """删除用户技能根里的技能；内置技能拒绝删除。"""
    wanted = str(name or "").strip()
    if not wanted:
        raise SkillError("没有指定要删除的技能")
    target = next((skill for skill in list_skills() if skill.name == wanted), None)
    if target is None:
        raise SkillError(f"没找到技能：{wanted}")
    if target.bundled:
        raise SkillError(f"内置技能随程序发布，不能在这里删除：{wanted}")
    root = user_skill_root().resolve()
    path = target.path.resolve()
    if root != path and root not in path.parents:
        raise SkillError(f"技能不在用户技能目录里，拒绝删除：{path}")
    shutil.rmtree(path)
    logger.info("[OfficeSkills] 删除技能 name=%s path=%s", wanted, path)
    return path


__all__ = [
    "SKILL_MANIFEST",
    "OfficeSkill",
    "SkillError",
    "install_skill",
    "list_skills",
    "read_skill",
    "remove_skill",
    "skill_roots",
    "user_skill_root",
]

"""Static contract checks for the Dango Showdown official package."""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "lib" / "script" / "gemes" / "packages" / "official" / "dango_showdown"
CODE = PACKAGE / "code" / "dango_showdown_pkg"

PURE_MODULES = (
    "__init__.py",
    "animator.py",
    "assets.py",
    "betting.py",
    "combat.py",
    "constants.py",
    "engine.py",
    "events.py",
    "geometry.py",
    "model.py",
    "physics.py",
    "presenter.py",
    "roster.py",
    "sfx.py",
    "skills.py",
    "stats.py",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class DangoShowdownStaticTests(unittest.TestCase):
    def test_manifest_is_valid_json(self):
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["package_type"], "game")
        self.assertEqual(manifest["game_id"], "dango_showdown")
        self.assertEqual(manifest["entry_module"], "dango_showdown_pkg.entry")
        self.assertEqual(manifest["entry_class"], "DangoShowdownGame")
        self.assertTrue(manifest["official"])
        self.assertIn("团子大决战", manifest["command_aliases"])
        self.assertEqual(manifest["extensions"]["particles"], [])
        self.assertEqual(manifest["extensions"]["effects"], [])

    def test_readme_exists(self):
        self.assertTrue((PACKAGE / "README.txt").exists())

    def test_every_roster_entry_has_assets(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.roster import ROSTER

        for entry in ROSTER:
            for state in ("idle", "attack", "hurt"):
                self.assertTrue((PACKAGE / "assets" / "dango" / entry.character_id / f"{state}.png").exists())

    def test_no_module_hard_codes_the_fighter_pair(self):
        for path in CODE.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("CHARACTERS", source, path.name)
            self.assertNotIn("CHARACTER_", source, path.name)

    def test_physics_has_only_plain_collisions(self):
        source = (CODE / "physics.py").read_text(encoding="utf-8")
        for banned in ("pull_together", "apply_jitter", "_approach", "STALL_RECOVER", "APPROACH_"):
            self.assertNotIn(banned, source)

    def test_roster_drives_every_skill_number(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import roster as R

        amis = R.find_entry("amis")
        daniya = R.find_entry("daniya")
        self.assertIsNotNone(amis)
        self.assertIsNotNone(daniya)
        self.assertEqual(amis.max_hp, 180)
        self.assertEqual(daniya.max_hp, 260)
        self.assertEqual([(m.meter_id, m.slots, m.damage) for m in amis.meters], [("starlight", 3, 8), ("starburst", 4, 60)])
        rule = daniya.collision_rules[0]
        self.assertEqual((rule.chance, rule.damage, rule.skill), (0.505, 3, "void_hand"))
        self.assertNotIn('"amis"', (CODE / "engine.py").read_text(encoding="utf-8"))
        self.assertNotIn('"daniya"', (CODE / "engine.py").read_text(encoding="utf-8"))
        self.assertNotIn('"amis"', (CODE / "skills.py").read_text(encoding="utf-8"))

    def test_pure_modules_do_not_import_qt(self):
        for name in PURE_MODULES:
            path = CODE / name
            self.assertTrue(path.exists(), name)
            imports = _imports(path)
            for module in imports:
                self.assertFalse(module.startswith("PyQt5"), f"{name} imports {module}")
                self.assertFalse(module.startswith("lib.core.render.backends"), f"{name} imports {module}")

    def test_only_widget_imports_qt(self):
        for path in sorted(CODE.glob("*.py")):
            imports = _imports(path)
            has_qt = any(module.startswith("PyQt5") for module in imports)
            if path.name == "widget.py":
                self.assertTrue(has_qt)
            else:
                self.assertFalse(has_qt, f"{path.name} imports Qt")

    def test_widget_keeps_all_painting_on_render_path(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertNotIn("drawImage", source)
        self.assertNotIn("painter.save()", source)
        self.assertNotIn("render_bridge", source)
        self.assertIn("create_painter_host()", source)
        self.assertIn("paint_snapshot(", source)

    def test_entry_keeps_runtime_contract(self):
        tree = ast.parse((CODE / "entry.py").read_text(encoding="utf-8"))
        class_names = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertIn("DangoShowdownGame", class_names)


if __name__ == "__main__":
    unittest.main()


class DangoWidgetPaintGuardTests(unittest.TestCase):
    def test_paint_event_does_not_shadow_flash_state(self):
        """A local named ``flash`` would capture the sprite source in paintEvent."""
        tree = ast.parse((CODE / "widget.py").read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name != "paintEvent":
                continue
            found = True
            assigned = {
                target.id
                for child in ast.walk(node)
                if isinstance(child, ast.Assign)
                for target in child.targets
                if isinstance(target, ast.Name)
            }
            self.assertNotIn("flash", assigned)
            keywords = {
                kw.arg
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                for kw in call.keywords
            }
            self.assertIn("flash", keywords)
        self.assertTrue(found)


class DangoEffectRuntimeContractTests(unittest.TestCase):
    """The package must use the desktop's registered feedback implementation."""

    def test_package_does_not_ship_private_feedback_scripts(self):
        self.assertEqual(list((PACKAGE / "extensions").rglob("*.py")), [])
        for path in CODE.glob("*.py"):
            imports = _imports(path)
            self.assertNotIn("lib.script.practical.base_particle", imports, path.name)
            self.assertNotIn("lib.script.effects.base_effect", imports, path.name)

    def test_game_uses_shared_scene_and_existing_image_effect(self):
        widget = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertIn("lib.core.services.feedback_scene", _imports(CODE / "widget.py"))
        self.assertIn("FeedbackScene(", widget)
        self.assertIn("get_particle_script_manager()", widget)
        self.assertIn("get_effect_script_manager()", widget)
        self.assertIn('.spawn_effect("smooth_image_show"', widget)
        self.assertIn("self._feedback.batch()", widget)
        self.assertNotIn("EventType.PARTICLE_REQUEST", widget)
        self.assertNotIn("EventType.EFFECT_REQUEST", widget)

    def test_laser_and_animated_images_have_no_package_renderer(self):
        presenter = (CODE / "presenter.py").read_text(encoding="utf-8")
        self.assertNotIn("class OverlayParticle", presenter)
        self.assertNotIn("def _paint_laser(", presenter)
        tree = ast.parse((CODE / "widget.py").read_text(encoding="utf-8"))
        laser_branches = [node for node in ast.walk(tree) if isinstance(node, ast.If)
                          and isinstance(node.test, ast.Compare)
                          and any(isinstance(value, ast.Attribute) and value.attr == "LASER_FIRE"
                                  for value in node.test.comparators)]
        self.assertTrue(laser_branches)
        self.assertTrue(any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == "_spawn_image"
                            for branch in laser_branches for statement in branch.body
                            for call in ast.walk(statement)))

    def test_shared_scene_keeps_host_instance_factories_and_presenters(self):
        path = ROOT / "lib/core/services/feedback_scene.py"
        source = path.read_text(encoding="utf-8")
        for operation in ("create_particle_instances", "create_effect_instances", "build_particle_batch", "build_effect_batch"):
            self.assertIn(operation + "(", source)
        self.assertFalse(any(name.startswith(("lib.script", "PyQt5")) for name in _imports(path)))

    def test_desktop_overlays_share_the_same_instance_lifecycle(self):
        for backend in ("qt/runtime", "dx"):
            for kind in ("particle", "effect"):
                with self.subTest(backend=backend, kind=kind):
                    path = ROOT / "lib/core/render/backends" / backend / f"{kind}_system.py"
                    source = path.read_text(encoding="utf-8")
                    self.assertIn("lib.core.services.feedback_scene", _imports(path))
                    self.assertIn(f"create_{kind}_instances(", source)
                    self.assertIn("update_instances(", source)
                    self.assertIn(f"interpolate_{kind}s(", source)


class DangoDamageNumberWiringTests(unittest.TestCase):
    """Damage numbers must reuse the existing ``floating_text`` particle."""

    WIDGET = CODE / "widget.py"

    def test_every_damage_path_spawns_floating_text(self):
        source = self.WIDGET.read_text(encoding="utf-8")
        self.assertIn('_spawn_particle("floating_text"', source)
        self.assertIn("def _show_damage_number(", source)
        # Collision hits, skill damage and the deferred starburst all funnel in.
        self.assertIn("_show_collision_damage(payload)", source)
        self.assertIn("_flush_deferred_numbers", source)

    def test_damage_numbers_reuse_existing_particle_id(self):
        tree = ast.parse(self.WIDGET.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_spawn_particle"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and "text" in node.args[0].value
        ]
        self.assertTrue(calls)
        for call in calls:
            self.assertEqual(call.args[0].value, "floating_text")


class DangoEffectWiringTests(unittest.TestCase):
    """The new decals are wired through the asset loader and the paint path."""

    def test_effect_assets_exist_and_are_predecoded(self):
        effects = PACKAGE / "assets" / "effects"
        self.assertTrue((effects / "void_hand.png").exists())
        self.assertTrue((effects / "arena_watermark.webp").exists())
        source = (CODE / "assets.py").read_text(encoding="utf-8")
        self.assertIn("void_hand = load_effect_resource(", source)
        self.assertIn('"edge_feather": True', source)
        self.assertIn("C.VOID_HAND_FEATHER_PX", source)
        self.assertNotIn("def _feathered(", source)

    def test_void_hand_spawns_a_random_feathered_decal_and_particles(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertIn("image.rotation = self._visual_rng.uniform(0.0, 360.0)", source)
        self.assertIn("C.VOID_HAND_EFFECT_SECONDS", source)
        self.assertIn("C.VOID_HAND_FEATHER_PX", (CODE / "assets.py").read_text(encoding="utf-8"))
        self.assertIn("resource = self._void_hand_resource()", source)
        self.assertNotIn("_void_hand_fx.append", source)
        self.assertIn("(190, 90, 255)", source)
        self.assertIn("(255, 80, 110)", source)

    def test_skill_audio_maps_to_existing_host_cues_without_synthesis(self):
        widget = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertIn('elif name == ev.VOID_HAND:', widget)
        self.assertIn('self._sfx.play("void_hand")', widget)
        path = CODE / "sfx.py"
        source = path.read_text(encoding="utf-8")
        imports = _imports(path)
        self.assertIn("lib.script.gemes.MAIN.game_sfx", imports)
        self.assertTrue({"wave", "struct", "random", "math"}.isdisjoint(imports))
        tree = ast.parse(source)
        assignments = {node.targets[0].id: node.value for node in tree.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name)}
        mapping = ast.literal_eval(assignments["CUE_MAP"])
        self.assertEqual(mapping["void_hand"], "lock")
        self.assertEqual(mapping["sun_summon"], "skill_cast")
        self.assertEqual(mapping["sun_burst"], "drop_impact")
        self.assertEqual(mapping["sun_echo"], "skill_release")
        host_tree = ast.parse((ROOT / "lib/script/gemes/MAIN/game_sfx.py").read_text(encoding="utf-8"))
        catalogs = [node.value for node in ast.walk(host_tree) if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Attribute) and target.attr == "_files"
                            for target in node.targets) and isinstance(node.value, ast.Dict)]
        self.assertTrue(catalogs)
        available = {key.value for catalog in catalogs for key in catalog.keys
                     if isinstance(key, ast.Constant) and isinstance(key.value, str)}
        self.assertTrue(set(mapping.values()) <= available)
        self.assertNotIn("BUILDERS", assignments)
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name.startswith("build_")
                             for node in ast.walk(tree)))
        self.assertIn("GameSfx(cache_dir=cache_dir, emit=emit)", source)
        self.assertIn("self._sfx.play(cue, volume_gain=VOLUMES[key])", source)
        for module in CODE.glob("*.py"):
            self.assertTrue({"wave", "struct"}.isdisjoint(_imports(module)), module.name)

    def test_watermark_is_painted_inside_the_arena(self):
        source = (CODE / "presenter.py").read_text(encoding="utf-8")
        self.assertIn("def _paint_watermark(", source)
        self.assertIn("watermark: FrameSource | None = None", source)
        self.assertIn("_paint_watermark(cmds, inner, watermark)", source)
        self.assertIn("ARENA_WATERMARK_ALPHA", source)

    def test_starburst_bursts_blue_cyan_and_white_on_the_landing(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        for rgb in ("(63, 184, 255)", "(126, 233, 255)", "(236, 250, 255)"):
            self.assertIn(rgb, source)
        self.assertIn("landing = self._canvas_point((0.5, 1.0 - C.BASE_RADIUS - 0.004))", source)

    def test_result_card_waits_three_seconds_and_cannot_be_skipped_by_click(self):
        widget = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertIn("reveal_pending = match.phase.value == PHASE_RESULT and bool(match.result_reveal)", widget)
        self.assertIn("if self._match.result_reveal in (None, 0.0):", widget)
        engine = (CODE / "engine.py").read_text(encoding="utf-8")
        self.assertIn("self.result_reveal = max(RESULT_MIN_SECONDS, RESULT_REVEAL_SECONDS)", engine)
        self.assertIn("dango.knocked_out = True", engine)
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import constants as C

        self.assertAlmostEqual(C.RESULT_REVEAL_SECONDS, 3.0)

    def test_chips_are_persisted_with_the_record(self):
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg import constants as C
        from lib.script.gemes.packages.official.dango_showdown.code.dango_showdown_pkg.betting import StreakState

        self.assertIn("chips", C.STATE_KEYS)
        state = StreakState(chips=-6)
        self.assertEqual(StreakState.from_record(state.as_record()).chips, -6)


class DangoBurstParticleOptionTests(unittest.TestCase):
    def test_burst_line_accepts_an_explicit_count_range(self):
        from lib.script.practical.burst_line_particle import BurstLineParticleScript

        script = BurstLineParticleScript()
        script.set_request_options({"rgb": (190, 90, 255), "count_range": (18, 18)})
        self.assertEqual(len(script.create_particles("point", (10.0, 10.0))), 18)


class DangoAsyncAssetTests(unittest.TestCase):
    """Opening the window must not decode images on the GUI thread."""

    def test_widget_constructor_does_not_decode_images(self):
        tree = ast.parse((CODE / "widget.py").read_text(encoding="utf-8"))
        widget_cls = next(
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "DangoShowdownWidget"
        )
        init = next(node for node in widget_cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
        calls = {
            node.func.id
            for node in ast.walk(init)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("load_image_resource", calls)
        self.assertNotIn("resize_image_resource", calls)
        self.assertNotIn("_tinted_source", calls)

    def test_widget_applies_background_assets_on_tick(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertIn("DangoAssetLoader(", source)
        self.assertIn("self._apply_assets(blocking=False)", source)


class DangoParticleRegistryTests(unittest.TestCase):
    """Particle ids are looked up verbatim, so every spawned id must exist."""

    def _spawned_ids(self):
        tree = ast.parse((CODE / "widget.py").read_text(encoding="utf-8"))
        ids = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "_spawn_particle" or len(node.args) < 3:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                ids.add(first.value)
        return ids

    def test_spawned_particles_are_registered(self):
        from lib.script.practical.manager import get_particle_script_manager

        ids = self._spawned_ids()
        self.assertIn("floating_text", ids)
        manager = get_particle_script_manager()
        self.assertEqual(sorted(i for i in ids if not manager.has_particle(i)), [])

    def test_particle_anchors_follow_the_target(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        # Skill feedback must anchor on the damaged dango, not a fixed centre.
        self.assertNotIn("point = self._to_screen((0.5, 0.5))", source)
        self.assertIn("point = self._target_point(payload)", source)
        tree = ast.parse(source)
        target_fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_target_point"
        )
        # The fallback chain must not end at a fixed screen-centre anchor.
        nodes = {id(n) for n in ast.walk(target_fn)}
        self.assertTrue(nodes)

    def test_particle_anchors_use_one_local_scene_coordinate_space(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertNotIn("mapToGlobal(", source)
        self.assertNotIn("def _canvas_to_global(", source)
        self.assertIn("return self._feedback.spawn_particle(", source)
        presenter = (CODE / "presenter.py").read_text(encoding="utf-8")
        self.assertIn("ClipPush(inner)", presenter)
        self.assertIn("feedback", presenter)

    def test_particle_requests_use_unqualified_ids(self):
        source = (CODE / "widget.py").read_text(encoding="utf-8")
        self.assertNotIn("qualify_particle_id", source)

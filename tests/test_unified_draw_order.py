from config.config_layer import LAYER_VALUES
from lib.core.draw_core import DrawCore, DrawRequest
from lib.core.layer import Layer, draw_order_key
from lib.core.layer_manager import LayerManager
from lib.core.render_core import RenderCore, order_render_values
from lib.core.render_layer import RenderRequest


class _Painter:
    def save(self):
        pass

    def restore(self):
        pass


class _Widget:
    def __init__(self, name, handle=1):
        self.name = name
        self.handle = handle

    def isVisible(self):
        return True

    def winId(self):
        return self.handle

    def raise_(self):
        pass


def test_draw_order_key_uses_generation_order_for_same_layer_and_z():
    earlier = draw_order_key(Layer.PET_UI, 0, 1)
    later = draw_order_key(Layer.PET_UI, 0, 2)

    assert earlier < later


def test_layer_enum_uses_configured_values():
    for name, configured_value in LAYER_VALUES.items():
        assert int(Layer[name]) == int(configured_value)


def test_draw_core_assigns_later_request_a_higher_generation_order():
    core = DrawCore()

    core.add_draw_request(DrawRequest('earlier', layer=Layer.MAIN_PET, z=0))
    core.add_draw_request(DrawRequest('later', layer=Layer.MAIN_PET, z=0))

    assert core._active_requests['earlier'].order < core._active_requests['later'].order


def test_draw_core_update_preserves_original_generation_order():
    core = DrawCore()

    core.add_draw_request(DrawRequest('earlier', layer=Layer.MAIN_PET, z=0))
    core.add_draw_request(DrawRequest('later', layer=Layer.MAIN_PET, z=0))
    core.add_draw_request(DrawRequest('earlier', layer=Layer.MAIN_PET, z=0))

    assert core._active_requests['earlier'].order < core._active_requests['later'].order


def test_render_core_draws_later_generated_item_last():
    rendered = []
    core = RenderCore()
    painter = _Painter()

    core.register_item(RenderRequest('earlier', lambda *_: rendered.append('earlier'), Layer.EFFECT, 0))
    core.register_item(RenderRequest('later', lambda *_: rendered.append('later'), Layer.EFFECT, 0))
    core.render(painter)

    assert rendered == ['earlier', 'later']


def test_render_core_update_preserves_original_generation_order():
    rendered = []
    core = RenderCore()
    painter = _Painter()

    core.register_item(RenderRequest('earlier', lambda *_: None, Layer.EFFECT, 0))
    core.register_item(RenderRequest('later', lambda *_: rendered.append('later'), Layer.EFFECT, 0))
    core.register_item(RenderRequest('earlier', lambda *_: rendered.append('earlier'), Layer.EFFECT, 0))
    core.render(painter)

    assert rendered == ['earlier', 'later']


def test_order_render_values_uses_layer_then_z_then_generation_order():
    values = [
        {'name': 'effect-high', 'layer': Layer.EFFECT, 'z': 5, 'order': 1},
        {'name': 'particle', 'layer': Layer.PARTICLE, 'z': 99, 'order': 2},
        {'name': 'effect-late', 'layer': Layer.EFFECT, 'z': 5, 'order': 3},
        {'name': 'effect-low', 'layer': Layer.EFFECT, 'z': 1, 'order': 4},
    ]

    ordered = order_render_values(
        values,
        layer_getter=lambda item: item['layer'],
        z_getter=lambda item: item['z'],
        order_getter=lambda item: item['order'],
    )

    assert [item['name'] for item in ordered] == [
        'particle',
        'effect-low',
        'effect-high',
        'effect-late',
    ]


def test_layer_manager_snapshot_orders_later_generated_window_last():
    manager = LayerManager()
    earlier = _Widget('earlier')
    later = _Widget('later')

    manager.register(earlier, Layer.PANEL, name='earlier')
    manager.register(later, Layer.PANEL, name='later')

    assert [row[3] for row in manager.snapshot()] == ['earlier', 'later']


def test_layer_manager_builds_explicit_topmost_chain_from_high_to_low():
    manager = LayerManager()
    calls = []
    manager._set_window_pos_api = lambda hwnd, insert_after, *_: calls.append((hwnd, insert_after))
    particle = _Widget('particle', 101)
    game = _Widget('game', 202)
    pet = _Widget('pet', 303)

    manager.register(particle, Layer.PARTICLE)
    manager.register(game, Layer.PANEL)
    manager.register(pet, Layer.MAIN_PET)
    manager.enforce_now()

    expected_windows = sorted(
        ((Layer.PARTICLE, 101), (Layer.PANEL, 202), (Layer.MAIN_PET, 303)),
        key=lambda item: int(item[0]),
        reverse=True,
    )
    expected = []
    insert_after = manager._HWND_TOPMOST
    for _, hwnd in expected_windows:
        expected.append((hwnd, insert_after))
        insert_after = hwnd

    assert calls == expected


def test_bring_to_front_preserves_higher_layer_windows():
    manager = LayerManager()
    calls = []
    manager._set_window_pos_api = lambda hwnd, insert_after, *_: calls.append((hwnd, insert_after))
    game = _Widget('game', 202)
    pet = _Widget('pet', 303)

    manager.register(game, Layer.PANEL)
    manager.register(pet, Layer.MAIN_PET)
    manager.bring_to_front(game)

    expected_windows = sorted(
        ((Layer.PANEL, 202), (Layer.MAIN_PET, 303)),
        key=lambda item: int(item[0]),
        reverse=True,
    )
    expected = []
    insert_after = manager._HWND_TOPMOST
    for _, hwnd in expected_windows:
        expected.append((hwnd, insert_after))
        insert_after = hwnd

    assert calls == expected

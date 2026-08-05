from config.config_layer import LAYER_VALUES
from lib.core.graphics.ordering import order_render_values
from lib.core.layer import Layer, draw_order_key
from lib.core.layer_manager import LayerManager
from lib.core.qt_bridge.render_core import QtRenderCore, QtRenderRequest


class _Painter:
    def save(self):
        pass

    def restore(self):
        pass


class _Window:
    def __init__(self, name, handle=1):
        self.name = name
        self.handle = handle
        self.visible = True
        self.alive = True
        self.native_stacking = True
        self.stack_calls = []
        self.raise_calls = 0


class _LayerHost:
    def __init__(self, window):
        self.window = window

    @property
    def identity(self):
        return id(self.window)

    def is_alive(self):
        return self.window.alive

    def is_visible(self):
        return self.window.visible

    def raise_window(self):
        self.window.raise_calls += 1

    def stack_window(self, insert_after):
        self.window.stack_calls.append((self.window.handle, insert_after))
        return self.window.handle if self.window.native_stacking else None


def _host_factory(window):
    return _LayerHost(window)


def test_draw_order_key_uses_generation_order_for_same_layer_and_z():
    earlier = draw_order_key(Layer.PET_UI, 0, 1)
    later = draw_order_key(Layer.PET_UI, 0, 2)

    assert earlier < later


def test_layer_enum_uses_configured_values():
    for name, configured_value in LAYER_VALUES.items():
        assert int(Layer[name]) == int(configured_value)


def test_qt_render_core_draws_later_generated_item_last():
    rendered = []
    core = QtRenderCore()
    painter = _Painter()

    core.register_item(QtRenderRequest('earlier', lambda *_: rendered.append('earlier'), Layer.EFFECT, 0))
    core.register_item(QtRenderRequest('later', lambda *_: rendered.append('later'), Layer.EFFECT, 0))
    core.render(painter)

    assert rendered == ['earlier', 'later']


def test_qt_render_core_update_preserves_original_generation_order():
    rendered = []
    core = QtRenderCore()
    painter = _Painter()

    core.register_item(QtRenderRequest('earlier', lambda *_: None, Layer.EFFECT, 0))
    core.register_item(QtRenderRequest('later', lambda *_: rendered.append('later'), Layer.EFFECT, 0))
    core.register_item(QtRenderRequest('earlier', lambda *_: rendered.append('earlier'), Layer.EFFECT, 0))
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
    manager = LayerManager(host_factory=_host_factory)
    earlier = _Window('earlier')
    later = _Window('later')

    manager.register(earlier, Layer.PANEL, name='earlier')
    manager.register(later, Layer.PANEL, name='later')

    assert [row[3] for row in manager.snapshot()] == ['earlier', 'later']


def test_layer_manager_builds_explicit_topmost_chain_from_high_to_low():
    manager = LayerManager(host_factory=_host_factory)
    particle = _Window('particle', 101)
    game = _Window('game', 202)
    pet = _Window('pet', 303)

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
    insert_after = None
    for _, hwnd in expected_windows:
        expected.append((hwnd, insert_after))
        insert_after = hwnd

    windows = {window.handle: window for window in (particle, game, pet)}
    calls = [
        call
        for _, hwnd in expected_windows
        for call in windows[hwnd].stack_calls
    ]
    assert calls == expected


def test_bring_to_front_preserves_higher_layer_windows():
    manager = LayerManager(host_factory=_host_factory)
    game = _Window('game', 202)
    pet = _Window('pet', 303)

    manager.register(game, Layer.PANEL)
    manager.register(pet, Layer.MAIN_PET)
    manager.bring_to_front(game)

    expected_windows = sorted(
        ((Layer.PANEL, 202), (Layer.MAIN_PET, 303)),
        key=lambda item: int(item[0]),
        reverse=True,
    )
    expected = []
    insert_after = None
    for _, hwnd in expected_windows:
        expected.append((hwnd, insert_after))
        insert_after = hwnd

    windows = {window.handle: window for window in (game, pet)}
    calls = [
        call
        for _, hwnd in expected_windows
        for call in windows[hwnd].stack_calls
    ]
    assert calls == expected


def test_layer_manager_prunes_dead_hosts_and_skips_hidden_hosts():
    manager = LayerManager(host_factory=_host_factory)
    dead = _Window('dead', 101)
    hidden = _Window('hidden', 202)
    visible = _Window('visible', 303)
    dead.alive = False
    hidden.visible = False

    manager.register(dead, Layer.PANEL, name='dead')
    manager.register(hidden, Layer.PANEL, name='hidden')
    manager.register(visible, Layer.PANEL, name='visible')
    manager.enforce_now()

    assert dead.stack_calls == []
    assert hidden.stack_calls == []
    assert visible.stack_calls == [(303, None)]
    assert [row[3] for row in manager.snapshot()] == ['hidden', 'visible']


def test_layer_manager_falls_back_to_ordered_raise_when_native_stacking_fails():
    manager = LayerManager(host_factory=_host_factory)
    lower = _Window('lower', 101)
    higher = _Window('higher', 202)
    higher.native_stacking = False

    manager.register(lower, Layer.PANEL)
    manager.register(higher, Layer.DIALOG)
    manager.enforce_now()

    assert higher.stack_calls == [(202, None)]
    assert lower.stack_calls == []
    assert lower.raise_calls == 1
    assert higher.raise_calls == 1

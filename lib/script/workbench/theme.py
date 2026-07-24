"""Design tokens and QSS for the unified workbench."""

from __future__ import annotations

from dataclasses import dataclass

from config.scale import scale_px


@dataclass(frozen=True)
class WorkbenchColors:
    canvas: str = "#0d0f12"
    navigation: str = "#121419"
    surface: str = "#17191f"
    surface_raised: str = "#1e2128"
    surface_hover: str = "#272b33"
    border: str = "#353a45"
    border_strong: str = "#4a515f"
    text: str = "#f4f5f7"
    text_muted: str = "#a8adb7"
    text_dim: str = "#777e8b"
    pink: str = "#ff95bc"
    pink_hover: str = "#ffb1cf"
    cyan: str = "#8cd2ff"
    warning: str = "#f1cf76"
    danger: str = "#ff7a92"


COLORS = WorkbenchColors()


def workbench_stylesheet() -> str:
    c = COLORS
    border = scale_px(1, min_abs=1)
    nav_width_padding = scale_px(11, min_abs=9)
    control_height = scale_px(32, min_abs=28)
    radius = scale_px(4, min_abs=3)
    return f"""
    QWidget#WorkbenchWindow {{
        background: transparent;
        color: {c.text};
    }}
    QFrame#WorkbenchShell {{
        background: {c.canvas};
        border: {border}px solid {c.border_strong};
    }}
    QFrame#WorkbenchHeader {{
        background: {c.surface};
        border: none;
        border-bottom: {border}px solid {c.border};
    }}
    QFrame#WorkbenchNavigation {{
        background: {c.navigation};
        border: none;
        border-right: {border}px solid {c.border};
    }}
    QFrame#WorkbenchContent {{
        background: {c.canvas};
        border: none;
    }}
    QWidget#WorkbenchPageHost, QWidget#WorkbenchPage {{
        background: transparent;
        border: none;
    }}
    QLabel#WorkbenchBrand {{
        color: {c.text};
        font-weight: 700;
    }}
    QLabel#WorkbenchBrandMeta, QLabel#WorkbenchHeaderGroup,
    QLabel#WorkbenchNavGroup, QLabel#WorkbenchPlaceholder {{
        color: {c.text_muted};
    }}
    QLabel#WorkbenchHeaderTitle {{
        color: {c.text};
        font-weight: 700;
    }}
    QLabel#WorkbenchNavGroup {{
        background: {c.surface};
        color: {c.cyan};
        border: none;
        border-top: {border}px solid {c.border};
        border-bottom: {border}px solid {c.border};
        padding: {scale_px(8, min_abs=6)}px {scale_px(13, min_abs=10)}px {scale_px(6, min_abs=5)}px {scale_px(13, min_abs=10)}px;
        margin-top: {scale_px(7, min_abs=5)}px;
        font-weight: 700;
    }}
    QPushButton#WorkbenchNavButton {{
        background: transparent;
        color: {c.text_muted};
        border: none;
        border-left: {scale_px(3, min_abs=2)}px solid transparent;
        border-radius: {radius}px;
        margin: {scale_px(1, min_abs=1)}px {scale_px(8, min_abs=6)}px;
        padding: {scale_px(8, min_abs=6)}px {nav_width_padding}px {scale_px(8, min_abs=6)}px {scale_px(18, min_abs=15)}px;
        text-align: left;
        font-weight: 600;
    }}
    QPushButton#WorkbenchNavButton:hover {{
        background: {c.surface_hover};
        color: {c.text};
    }}
    QPushButton#WorkbenchNavButton:checked {{
        background: {c.surface_raised};
        color: {c.text};
        border-left-color: {c.pink};
    }}
    QToolButton#WorkbenchWindowButton {{
        background: transparent;
        border: none;
        border-radius: {radius}px;
        min-width: {control_height}px;
        min-height: {control_height}px;
        max-width: {control_height}px;
        max-height: {control_height}px;
        padding: 0px;
    }}
    QToolButton#WorkbenchAboutButton {{
        background: transparent;
        border: none;
        padding: 0px;
    }}
    QToolButton#WorkbenchWindowButton:hover {{
        background: {c.surface_hover};
    }}
    QToolButton#WorkbenchWindowButton[danger="true"]:hover {{
        background: {c.danger};
    }}
    QCheckBox#WorkbenchPinToggle {{
        color: {c.text_muted};
        spacing: {scale_px(6, min_abs=4)}px;
        padding: 0px {scale_px(4, min_abs=3)}px;
    }}
    QCheckBox#WorkbenchPinToggle:hover {{
        color: {c.text};
    }}
    QCheckBox#WorkbenchPinToggle::indicator {{
        width: {scale_px(14, min_abs=12)}px;
        height: {scale_px(14, min_abs=12)}px;
        border: {border}px solid {c.border_strong};
        border-radius: {scale_px(2, min_abs=2)}px;
        background: {c.surface};
    }}
    QCheckBox#WorkbenchPinToggle::indicator:checked {{
        background: {c.cyan};
        border-color: {c.cyan};
    }}
    QLineEdit#WorkbenchSearch {{
        background: {c.canvas};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(10, min_abs=8)}px;
        selection-background-color: {c.cyan};
        selection-color: {c.canvas};
    }}
    QLineEdit#WorkbenchSearch:focus {{
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QLabel {{
        color: {c.text};
    }}
    QFrame#WorkbenchPageHost QPushButton {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(11, min_abs=9)}px;
        font-weight: 600;
    }}
    QFrame#WorkbenchPageHost QPushButton:hover {{
        background: {c.surface_hover};
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QPushButton:pressed {{
        background: {c.cyan};
        color: {c.canvas};
    }}
    QFrame#WorkbenchPageHost QPushButton:disabled {{
        background: {c.surface};
        color: {c.text_dim};
        border-color: {c.border};
    }}
    QFrame#WorkbenchPageHost QPushButton#ContributionCardButton {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {scale_px(74, min_abs=66)}px;
        padding: 0px;
        text-align: left;
    }}
    QFrame#WorkbenchPageHost QPushButton#ContributionCardButton:hover {{
        background: {c.surface_hover};
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QPushButton#ContributionCardButton:pressed {{
        background: {c.surface};
        color: {c.text};
        border-color: {c.pink};
    }}
    QFrame#WorkbenchPageHost QWidget#ContributionCardAccent {{
        background: {c.cyan};
        border: none;
        border-radius: {scale_px(1, min_abs=1)}px;
    }}
    QFrame#WorkbenchPageHost QLabel#ContributionCardName {{
        background: transparent;
        color: {c.text};
    }}
    QFrame#WorkbenchPageHost QLabel#ContributionCardRole {{
        background: transparent;
        color: {c.text_muted};
    }}
    QFrame#WorkbenchPageHost QWidget#sponsorAuthorCard {{
        background: transparent;
        border: none;
    }}
    QFrame#WorkbenchPageHost QWidget#sponsorAuthorImageFrame {{
        background: {c.surface_raised};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
    }}
    QFrame#WorkbenchPageHost QLabel#sponsorAuthorImage {{
        background: transparent;
        color: {c.text};
        padding: {scale_px(6, min_abs=4)}px;
    }}
    QFrame#WorkbenchPageHost QPushButton#sponsorAuthorButton {{
        background: {c.cyan};
        color: {c.canvas};
        border-color: {c.cyan};
        min-height: {scale_px(36, min_abs=32)}px;
    }}
    QFrame#WorkbenchPageHost QPushButton#sponsorAuthorButton:hover {{
        background: {c.pink_hover};
        color: {c.canvas};
        border-color: {c.pink_hover};
    }}
    QFrame#WorkbenchPageHost QLineEdit,
    QFrame#WorkbenchPageHost QComboBox,
    QFrame#WorkbenchPageHost QSpinBox,
    QFrame#WorkbenchPageHost QDoubleSpinBox,
    QFrame#WorkbenchPageHost QTextEdit,
    QFrame#WorkbenchPageHost QPlainTextEdit {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {control_height}px;
        padding: 0px {scale_px(9, min_abs=7)}px;
        selection-background-color: {c.cyan};
        selection-color: {c.canvas};
    }}
    QFrame#WorkbenchPageHost QLineEdit:focus,
    QFrame#WorkbenchPageHost QComboBox:focus,
    QFrame#WorkbenchPageHost QSpinBox:focus,
    QFrame#WorkbenchPageHost QDoubleSpinBox:focus,
    QFrame#WorkbenchPageHost QTextEdit:focus,
    QFrame#WorkbenchPageHost QPlainTextEdit:focus {{
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QComboBox::drop-down {{
        width: {scale_px(32, min_abs=28)}px;
        border: none;
        border-left: {border}px solid {c.border};
    }}
    QFrame#WorkbenchPageHost QComboBox::down-arrow {{
        image: url(resc/ui/combo_down_arrow.svg);
        width: {scale_px(12, min_abs=10)}px;
        height: {scale_px(8, min_abs=6)}px;
    }}
    QFrame#WorkbenchPageHost QComboBox QAbstractItemView {{
        background: {c.surface};
        color: {c.text};
        border: {border}px solid {c.border_strong};
        selection-background-color: {c.surface_hover};
        selection-color: {c.pink};
        outline: none;
    }}
    QFrame#WorkbenchPageHost QListWidget,
    QFrame#WorkbenchPageHost QTreeWidget,
    QFrame#WorkbenchPageHost QTableWidget {{
        background: {c.surface};
        alternate-background-color: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        outline: none;
    }}
    QFrame#WorkbenchPageHost QListWidget::item:selected,
    QFrame#WorkbenchPageHost QTreeWidget::item:selected,
    QFrame#WorkbenchPageHost QTableWidget::item:selected {{
        background: {c.surface_hover};
        color: {c.pink};
    }}
    QFrame#WorkbenchPageHost QCheckBox {{
        color: {c.text};
        spacing: {scale_px(7, min_abs=5)}px;
    }}
    QFrame#WorkbenchPageHost QCheckBox::indicator {{
        width: {scale_px(15, min_abs=13)}px;
        height: {scale_px(15, min_abs=13)}px;
        background: {c.surface_raised};
        border: {border}px solid {c.border_strong};
        border-radius: {scale_px(2, min_abs=2)}px;
    }}
    QFrame#WorkbenchPageHost QCheckBox::indicator:checked {{
        background: {c.cyan};
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QSlider::groove:horizontal {{
        height: {scale_px(5, min_abs=4)}px;
        background: {c.border};
        border: none;
        border-radius: {scale_px(2, min_abs=2)}px;
    }}
    QFrame#WorkbenchPageHost QSlider::handle:horizontal {{
        width: {scale_px(15, min_abs=13)}px;
        margin: -{scale_px(5, min_abs=4)}px 0px;
        background: {c.pink};
        border: {border}px solid {c.canvas};
        border-radius: {scale_px(7, min_abs=6)}px;
    }}
    QFrame#WorkbenchPageHost QScrollArea {{
        background: transparent;
        border: none;
    }}
    QFrame#WorkbenchPageHost QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QFrame#WorkbenchPageHost QFrame#SettingsPageHeader {{
        background: transparent;
        border: none;
    }}
    QFrame#WorkbenchPageHost QLabel#SettingsPageTitle {{
        color: {c.text};
        font-weight: 700;
    }}
    QFrame#WorkbenchPageHost QLabel#SettingsPageDescription,
    QFrame#WorkbenchPageHost QLabel#SettingsSectionDescription {{
        color: {c.text_muted};
    }}
    QFrame#WorkbenchPageHost QFrame#SettingsSection {{
        background: {c.surface};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
    }}
    QFrame#WorkbenchPageHost QLabel#SettingsSectionTitle {{
        color: {c.text};
        font-weight: 700;
    }}
    QFrame#WorkbenchPageHost QLabel#ConfigFormLabel {{
        color: {c.text_muted};
        font-weight: 600;
    }}
    QFrame#WorkbenchPageHost QFrame#SettingsActionBar {{
        background: {c.navigation};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
    }}
    QFrame#WorkbenchPageHost QPushButton#SettingsPrimaryAction,
    QFrame#WorkbenchPageHost QPushButton[primary="true"] {{
        background: {c.cyan};
        color: {c.canvas};
        border-color: {c.cyan};
    }}
    QFrame#WorkbenchPageHost QPushButton#SettingsPrimaryAction:hover,
    QFrame#WorkbenchPageHost QPushButton[primary="true"]:hover {{
        background: {c.pink_hover};
        color: {c.canvas};
        border-color: {c.pink_hover};
    }}
    QScrollArea#WorkbenchNavigationScroll {{
        background: transparent;
        border: none;
    }}
    QScrollArea#WorkbenchNavigationScroll > QWidget > QWidget {{
        background: transparent;
    }}
    QFrame#WorkbenchOverviewSection {{
        background: {c.surface};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
    }}
    QLabel#WorkbenchOverviewSectionTitle {{
        color: {c.text};
        font-weight: 700;
    }}
    QFrame#WorkbenchPageHost QPushButton#WorkbenchQuickAction {{
        background: {c.surface_raised};
        color: {c.text};
        border: {border}px solid {c.border};
        border-radius: {radius}px;
        min-height: {scale_px(42, min_abs=36)}px;
        padding: 0px {scale_px(12, min_abs=10)}px;
        text-align: left;
        font-weight: 600;
    }}
    QFrame#WorkbenchPageHost QPushButton#WorkbenchQuickAction:hover {{
        background: {c.surface_hover};
        border-color: {c.cyan};
    }}
    QMenu {{
        background: {c.surface};
        color: {c.text};
        border: {border}px solid {c.border_strong};
        padding: {scale_px(4, min_abs=3)}px;
    }}
    QMenu::item {{
        padding: {scale_px(7, min_abs=6)}px {scale_px(22, min_abs=18)}px;
    }}
    QMenu::item:selected {{
        background: {c.surface_hover};
        color: {c.pink};
    }}
    QMenu#WorkbenchAboutMenu {{
        min-width: {scale_px(148, min_abs=132)}px;
    }}
    QMenu#WorkbenchAboutMenu::item {{
        padding: {scale_px(9, min_abs=7)}px {scale_px(20, min_abs=16)}px;
        font-weight: 600;
    }}
    QStackedWidget#WorkbenchStack {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: {c.canvas};
        width: {scale_px(10, min_abs=8)}px;
        border: none;
    }}
    QScrollBar::handle:vertical {{
        background: {c.border_strong};
        min-height: {scale_px(28, min_abs=22)}px;
        border-radius: {scale_px(3, min_abs=2)}px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c.text_dim};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        border: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """

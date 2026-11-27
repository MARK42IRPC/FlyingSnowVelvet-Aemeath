#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN

#include <windows.h>
#include <windowsx.h>
#include <commctrl.h>
#include <wchar.h>

#include "installer_theme.h"
#include "native_notice.h"
#include "resource.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "user32.lib")

#define IDC_NOTICE_DISMISS 3001
#define IDC_NOTICE_CONFIRM 3002
#define IDC_NOTICE_CLOSE 3003

static const wchar_t NOTICE_CLASS_NAME[] = L"FlyingSnowVelvetNativeNotice";

typedef enum FsvNoticeResult {
    NOTICE_UNAVAILABLE,
    NOTICE_DISMISSED,
    NOTICE_CONFIRMED,
} FsvNoticeResult;

typedef struct FsvNotice {
    const wchar_t *title;
    const wchar_t *source;
    HWND body_edit;
    HWND dismiss;
    HWND confirm;
    HWND close;
    HWND hovered;
    HFONT title_font;
    HFONT body_font;
    HFONT action_font;
    HFONT source_font;
    HBRUSH surface_brush;
    UINT dpi;
    BOOL destructive;
    FsvNoticeResult result;
    RECT body_rect;
    RECT close_rect;
} FsvNotice;

HANDLE fsv_register_embedded_ui_font(void) {
    HINSTANCE instance = GetModuleHandleW(NULL);
    HRSRC resource = FindResourceW(instance, MAKEINTRESOURCEW(IDR_HARMONY_FONT), RT_RCDATA);
    HGLOBAL loaded = resource != NULL ? LoadResource(instance, resource) : NULL;
    void *data = loaded != NULL ? LockResource(loaded) : NULL;
    DWORD size = resource != NULL ? SizeofResource(instance, resource) : 0;
    DWORD font_count = 0;
    return size != 0 && data != NULL ? AddFontMemResourceEx(data, size, NULL, &font_count) : NULL;
}

static int notice_px(const FsvNotice *notice, int value) {
    return MulDiv(value, (int)notice->dpi, 96);
}

static HFONT notice_font(const FsvNotice *notice, int size, int weight) {
    return CreateFontW(-notice_px(notice, size), 0, 0, 0, weight, FALSE, FALSE, FALSE,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
        DEFAULT_PITCH, L"HarmonyOS Sans SC");
}

static HFONT notice_font_or_default(HFONT font) {
    return font != NULL ? font : (HFONT)GetStockObject(DEFAULT_GUI_FONT);
}

static void notice_fill(HDC dc, const RECT *bounds, COLORREF color) {
    SetDCBrushColor(dc, color);
    FillRect(dc, bounds, (HBRUSH)GetStockObject(DC_BRUSH));
}

static void notice_panel(HDC dc, const RECT *bounds, COLORREF fill, COLORREF border,
                         int radius, int outline) {
    HPEN pen = CreatePen(PS_SOLID, outline, border);
    HGDIOBJ old_pen = SelectObject(dc, pen != NULL ? pen : GetStockObject(NULL_PEN));
    HGDIOBJ old_brush = SelectObject(dc, GetStockObject(DC_BRUSH));
    SetDCBrushColor(dc, fill);
    RoundRect(dc, bounds->left, bounds->top, bounds->right, bounds->bottom, radius * 2, radius * 2);
    SelectObject(dc, old_pen);
    SelectObject(dc, old_brush);
    if (pen != NULL) {
        DeleteObject(pen);
    }
}

static void notice_text(HDC dc, HFONT font, COLORREF color, const wchar_t *text, RECT bounds,
                        UINT format) {
    HGDIOBJ old_font = SelectObject(dc, notice_font_or_default(font));
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, color);
    DrawTextW(dc, text, -1, &bounds, format | DT_NOPREFIX);
    SelectObject(dc, old_font);
}

static void notice_paint(HWND window, HDC dc, const FsvNotice *notice) {
    RECT client;
    RECT inner;
    int margin = notice_px(notice, FSV_POPUP_MARGIN_X);
    int header_top = notice_px(notice, FSV_POPUP_MARGIN_TOP);
    int header_bottom = header_top + notice_px(notice, FSV_POPUP_HEADER_HEIGHT);
    int border = notice_px(notice, FSV_POPUP_BORDER);
    int accent_end = margin + notice_px(notice, FSV_POPUP_ACCENT_WIDTH);
    int separator = max(margin, accent_end - border);
    RECT pink = {margin, header_top, separator, header_bottom};
    RECT cyan = {separator, header_top, accent_end, header_bottom};
    RECT heading = {accent_end + notice_px(notice, FSV_POPUP_HEADER_GAP), header_top,
                    notice->close_rect.left - notice_px(notice, FSV_POPUP_HEADER_GAP), header_bottom};
    if (!GetClientRect(window, &client)) {
        return;
    }
    inner = client;
    InflateRect(&inner, -border, -border);
    notice_fill(dc, &client, FSV_COLOR_BORDER_STRONG);
    notice_fill(dc, &inner, FSV_COLOR_CANVAS);
    notice_fill(dc, &pink, FSV_COLOR_PINK);
    notice_fill(dc, &cyan, FSV_COLOR_CYAN);
    if (notice->source != NULL) {
        RECT source = heading;
        heading.bottom = heading.top + notice_px(notice, FSV_POPUP_TITLE_SIZE + FSV_POPUP_HEADER_GAP);
        source.top = heading.bottom;
        notice_text(dc, notice->source_font, FSV_COLOR_TEXT_DIM, notice->source, source,
            DT_LEFT | DT_TOP | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
    notice_text(dc, notice->title_font, FSV_COLOR_TEXT, notice->title, heading,
        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    notice_panel(dc, &notice->body_rect, FSV_COLOR_SURFACE, FSV_COLOR_BORDER,
        notice_px(notice, FSV_POPUP_RADIUS), border);
}

typedef struct NoticeButtonColors {
    COLORREF fill;
    COLORREF border;
    COLORREF text;
} NoticeButtonColors;

static const NoticeButtonColors NOTICE_BUTTON_DEFAULT[] = {
    FSV_POPUP_ACTION_DEFAULT_NORMAL, FSV_POPUP_ACTION_DEFAULT_HOVER, FSV_POPUP_ACTION_DEFAULT_PRESSED,
};
static const NoticeButtonColors NOTICE_BUTTON_PRIMARY[] = {
    FSV_POPUP_ACTION_PRIMARY_NORMAL, FSV_POPUP_ACTION_PRIMARY_HOVER, FSV_POPUP_ACTION_PRIMARY_PRESSED,
};
static const NoticeButtonColors NOTICE_BUTTON_DANGER[] = {
    FSV_POPUP_ACTION_DANGER_NORMAL, FSV_POPUP_ACTION_DANGER_HOVER, FSV_POPUP_ACTION_DANGER_PRESSED,
};
static const NoticeButtonColors NOTICE_BUTTON_GHOST[] = {
    FSV_POPUP_ACTION_GHOST_NORMAL, FSV_POPUP_ACTION_GHOST_HOVER, FSV_POPUP_ACTION_GHOST_PRESSED,
};
static const NoticeButtonColors NOTICE_BUTTON_DISABLED = FSV_POPUP_ACTION_DISABLED;

static void notice_draw_button(const DRAWITEMSTRUCT *item, const FsvNotice *notice) {
    BOOL close = item->CtlID == IDC_NOTICE_CLOSE;
    BOOL primary = item->CtlID == IDC_NOTICE_CONFIRM ||
        (item->CtlID == IDC_NOTICE_DISMISS && notice->confirm == NULL);
    const NoticeButtonColors *style = NOTICE_BUTTON_DEFAULT;
    size_t state = 0;
    NoticeButtonColors colors;
    RECT rect = item->rcItem;
    wchar_t label[64] = L"";
    if (close) {
        style = NOTICE_BUTTON_GHOST;
    } else if (primary) {
        style = notice->destructive ? NOTICE_BUTTON_DANGER : NOTICE_BUTTON_PRIMARY;
    }
    if ((item->itemState & ODS_SELECTED) != 0) {
        state = 2;
    } else if (item->hwndItem == notice->hovered) {
        state = 1;
    }
    colors = (item->itemState & ODS_DISABLED) != 0 ? NOTICE_BUTTON_DISABLED : style[state];
    if ((item->itemState & ODS_FOCUS) != 0) {
        colors.border = FSV_COLOR_CYAN;
    }
    notice_fill(item->hDC, &rect, FSV_COLOR_CANVAS);
    if (colors.fill != CLR_INVALID || colors.border != CLR_INVALID) {
        notice_panel(item->hDC, &rect,
            colors.fill != CLR_INVALID ? colors.fill : FSV_COLOR_CANVAS,
            colors.border != CLR_INVALID ? colors.border : FSV_COLOR_CANVAS,
            notice_px(notice, FSV_POPUP_RADIUS), notice_px(notice, FSV_POPUP_BORDER));
    }
    if (close) {
        notice_text(item->hDC, notice->title_font, colors.text, L"×", rect,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    } else {
        GetWindowTextW(item->hwndItem, label, ARRAYSIZE(label));
        notice_text(item->hDC, notice->action_font, colors.text, label, rect,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
}

static int notice_button_width(HDC dc, HWND button, int padding, int min_width) {
    wchar_t label[64];
    int length = GetWindowTextW(button, label, ARRAYSIZE(label));
    SIZE size = {0};
    GetTextExtentPoint32W(dc, label, length, &size);
    return max(min_width, size.cx + 2 * padding);
}

static BOOL notice_layout(HWND window, FsvNotice *notice, const RECT *anchor, const RECT *work) {
    HFONT old_title = notice->title_font;
    HFONT old_body = notice->body_font;
    HFONT old_action = notice->action_font;
    HFONT old_source = notice->source_font;
    HDC dc;
    HGDIOBJ old_font;
    TEXTMETRICW body_metrics = {0};
    int margin = notice_px(notice, FSV_POPUP_MARGIN_X);
    int padding = notice_px(notice, FSV_POPUP_BODY_PADDING);
    int top = notice_px(notice, FSV_POPUP_MARGIN_TOP);
    int bottom = notice_px(notice, FSV_POPUP_MARGIN_BOTTOM);
    int gap = notice_px(notice, FSV_POPUP_ROW_GAP);
    int action_gap = notice_px(notice, FSV_POPUP_ACTION_GAP);
    int action_padding = notice_px(notice, FSV_POPUP_ACTION_PAD_X);
    int button_height = notice_px(notice, FSV_POPUP_ACTION_HEIGHT);
    int close_size = notice_px(notice, FSV_POPUP_CLOSE_SIZE);
    int body_top = top + notice_px(notice, FSV_POPUP_HEADER_HEIGHT) + gap;
    int width = min(notice_px(notice, FSV_POPUP_NOTICE_WIDTH), work->right - work->left);
    int height = min(notice_px(notice, FSV_POPUP_NOTICE_HEIGHT), work->bottom - work->top);
    int button_y = height - bottom - button_height;
    int body_bottom = button_y - gap;
    int text_width = width - 2 * (margin + padding);
    int text_height = body_bottom - body_top - 2 * padding;
    int dismiss_width, confirm_width = 0;
    int button_x, x, y;
    if (text_width < close_size || text_height < notice_px(notice, FSV_POPUP_BODY_SIZE * 2)) {
        return FALSE;
    }
    dc = GetDC(window);
    if (dc == NULL) {
        return FALSE;
    }
    notice->title_font = notice_font(notice, FSV_POPUP_TITLE_SIZE, FW_BOLD);
    notice->body_font = notice_font(notice, FSV_POPUP_BODY_SIZE, FW_NORMAL);
    notice->action_font = notice_font(notice, FSV_POPUP_ACTION_SIZE, FW_BOLD);
    notice->source_font = notice->source != NULL ? notice_font(notice, FSV_POPUP_SOURCE_SIZE, FW_NORMAL) : NULL;
    old_font = SelectObject(dc, notice_font_or_default(notice->action_font));
    dismiss_width = notice_button_width(dc, notice->dismiss, action_padding, button_height);
    if (notice->confirm != NULL) {
        confirm_width = notice_button_width(dc, notice->confirm, action_padding, button_height);
    }
    SelectObject(dc, notice_font_or_default(notice->body_font));
    GetTextMetricsW(dc, &body_metrics);
    SelectObject(dc, old_font);
    ReleaseDC(window, dc);
    SendMessageW(notice->body_edit, WM_SETFONT, (WPARAM)notice_font_or_default(notice->body_font), FALSE);
    SendMessageW(notice->body_edit, EM_SETMARGINS, EC_LEFTMARGIN | EC_RIGHTMARGIN, 0);
    SendMessageW(notice->dismiss, WM_SETFONT, (WPARAM)notice_font_or_default(notice->action_font), FALSE);
    if (notice->confirm != NULL) {
        SendMessageW(notice->confirm, WM_SETFONT, (WPARAM)notice_font_or_default(notice->action_font), FALSE);
    }
    DeleteObject(old_title);
    DeleteObject(old_body);
    DeleteObject(old_action);
    DeleteObject(old_source);
    if (dismiss_width + confirm_width + (notice->confirm != NULL ? action_gap : 0) > width - 2 * margin) {
        return FALSE;
    }

    SetRect(&notice->body_rect, margin, body_top, width - margin, body_bottom);
    SetRect(&notice->close_rect, width - margin - close_size, top, width - margin, top + close_size);
    x = anchor->left + (anchor->right - anchor->left - width) / 2;
    y = anchor->top + (anchor->bottom - anchor->top - height) / 2;
    x = max(work->left, min(x, work->right - width));
    y = max(work->top, min(y, work->bottom - height));
    if (!SetWindowPos(window, NULL, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE)) {
        return FALSE;
    }
    ShowScrollBar(notice->body_edit, SB_VERT, FALSE);
    MoveWindow(notice->body_edit, margin + padding, body_top + padding, text_width, text_height, TRUE);
    ShowScrollBar(notice->body_edit, SB_VERT, SendMessageW(notice->body_edit, EM_GETLINECOUNT, 0, 0) >
        text_height / max(1, body_metrics.tmHeight));
    MoveWindow(notice->close, notice->close_rect.left, notice->close_rect.top, close_size, close_size, TRUE);
    button_x = width - margin;
    if (notice->confirm != NULL) {
        button_x -= confirm_width;
        MoveWindow(notice->confirm, button_x, button_y, confirm_width, button_height, TRUE);
        button_x -= action_gap;
    }
    MoveWindow(notice->dismiss, button_x - dismiss_width, button_y, dismiss_width, button_height, TRUE);
    InvalidateRect(window, NULL, FALSE);
    return TRUE;
}

static LRESULT CALLBACK notice_button_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam,
                                           UINT_PTR id, DWORD_PTR data) {
    FsvNotice *notice = (FsvNotice *)data;
    if (message == WM_MOUSEMOVE && notice->hovered != window) {
        TRACKMOUSEEVENT tracking = {sizeof(tracking), TME_LEAVE, window, 0};
        if (notice->hovered != NULL) {
            InvalidateRect(notice->hovered, NULL, FALSE);
        }
        notice->hovered = window;
        TrackMouseEvent(&tracking);
        InvalidateRect(window, NULL, FALSE);
    } else if (message == WM_MOUSELEAVE) {
        if (notice->hovered == window) {
            notice->hovered = NULL;
        }
        InvalidateRect(window, NULL, FALSE);
    } else if (message == WM_NCDESTROY) {
        RemoveWindowSubclass(window, notice_button_proc, id);
    }
    return DefSubclassProc(window, message, wparam, lparam);
}

static LRESULT CALLBACK notice_proc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    FsvNotice *notice = (FsvNotice *)GetWindowLongPtrW(window, GWLP_USERDATA);
    if (message == WM_NCCREATE) {
        const CREATESTRUCTW *create = (const CREATESTRUCTW *)lparam;
        SetWindowLongPtrW(window, GWLP_USERDATA, (LONG_PTR)create->lpCreateParams);
        return TRUE;
    }
    if (notice == NULL) {
        return DefWindowProcW(window, message, wparam, lparam);
    }
    switch (message) {
        case WM_PAINT: {
            PAINTSTRUCT paint;
            HDC dc = BeginPaint(window, &paint);
            notice_paint(window, dc, notice);
            EndPaint(window, &paint);
            return 0;
        }
        case WM_PRINTCLIENT:
            notice_paint(window, (HDC)wparam, notice);
            return 0;
        case WM_ERASEBKGND:
            return 1;
        case WM_CTLCOLORSTATIC:
            SetBkMode((HDC)wparam, OPAQUE);
            SetBkColor((HDC)wparam, FSV_COLOR_SURFACE);
            SetTextColor((HDC)wparam, FSV_COLOR_TEXT_MUTED);
            return (LRESULT)notice->surface_brush;
        case WM_DRAWITEM:
            if (lparam != 0) {
                notice_draw_button((const DRAWITEMSTRUCT *)lparam, notice);
                return TRUE;
            }
            break;
        case WM_DPICHANGED:
            if (notice->dismiss != NULL && lparam != 0) {
                const RECT *suggested = (const RECT *)lparam;
                MONITORINFO info = {sizeof(info)};
                notice->dpi = max(96U, HIWORD(wparam));
                if (GetMonitorInfoW(MonitorFromRect(suggested, MONITOR_DEFAULTTONEAREST), &info)) {
                    notice_layout(window, notice, suggested, &info.rcWork);
                }
            }
            return 0;
        case WM_NCHITTEST: {
            POINT point = {GET_X_LPARAM(lparam), GET_Y_LPARAM(lparam)};
            ScreenToClient(window, &point);
            if (point.y >= 0 && point.y < notice->body_rect.top && !PtInRect(&notice->close_rect, point)) {
                return HTCAPTION;
            }
            break;
        }
        case WM_COMMAND:
            if (notice->result == NOTICE_UNAVAILABLE && HIWORD(wparam) == BN_CLICKED && lparam != 0) {
                HWND button = (HWND)lparam;
                if (button == notice->confirm || button == notice->dismiss || button == notice->close) {
                    notice->result = button == notice->confirm ? NOTICE_CONFIRMED : NOTICE_DISMISSED;
                    return 0;
                }
            }
            break;
        case WM_CLOSE:
            if (notice->result == NOTICE_UNAVAILABLE) {
                notice->result = NOTICE_DISMISSED;
            }
            return 0;
        case WM_NCDESTROY:
            SetWindowLongPtrW(window, GWLP_USERDATA, 0);
            break;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}

static BOOL notice_register_class(HINSTANCE instance) {
    WNDCLASSW window_class = {0};
    window_class.lpfnWndProc = notice_proc;
    window_class.hInstance = instance;
    window_class.hCursor = LoadCursorW(NULL, IDC_ARROW);
    window_class.hIcon = LoadIconW(instance, MAKEINTRESOURCEW(IDI_INSTALLER));
    window_class.lpszClassName = NOTICE_CLASS_NAME;
    return RegisterClassW(&window_class) != 0 || GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
}

static HWND notice_button(HWND window, int id, const wchar_t *label, FsvNotice *notice, HINSTANCE instance) {
    HWND button = CreateWindowExW(0, L"BUTTON", label, WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
        0, 0, 1, 1, window, (HMENU)(INT_PTR)id, instance, NULL);
    if (button != NULL) {
        SetWindowSubclass(button, notice_button_proc, (UINT_PTR)id, (DWORD_PTR)notice);
    }
    return button;
}

static BOOL notice_anchor(HWND owner, RECT *anchor, MONITORINFO *info) {
    HMONITOR monitor;
    if (owner != NULL) {
        if (GetWindowThreadProcessId(owner, NULL) != GetCurrentThreadId() ||
            !IsWindowEnabled(owner) || !GetWindowRect(owner, anchor)) {
            return FALSE;
        }
        monitor = MonitorFromWindow(owner, MONITOR_DEFAULTTONEAREST);
    } else {
        POINT cursor = {0};
        GetCursorPos(&cursor);
        monitor = MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST);
    }
    if (!GetMonitorInfoW(monitor, info)) {
        return FALSE;
    }
    if (owner == NULL) {
        *anchor = info->rcWork;
    }
    return TRUE;
}

static FsvNoticeResult notice_run(HWND owner, const wchar_t *title, const wchar_t *body,
                                  const wchar_t *confirm_label, BOOL destructive, const wchar_t *source) {
    FsvNotice notice = {0};
    FsvNoticeResult result = NOTICE_UNAVAILABLE;
    HINSTANCE instance = GetModuleHandleW(NULL);
    HWND previous_focus = GetFocus();
    HWND window = NULL;
    HANDLE embedded_font = NULL;
    DPI_AWARENESS_CONTEXT prior_dpi = NULL;
    MONITORINFO info = {sizeof(info)};
    RECT anchor;
    MSG message = {0};
    int status = 1;
    if (title == NULL || body == NULL || (confirm_label != NULL && owner == NULL)) {
        return result;
    }
    if (owner == NULL) {
        prior_dpi = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    }
    if (!notice_anchor(owner, &anchor, &info) || !notice_register_class(instance)) {
        goto cleanup;
    }
    notice.title = title;
    notice.source = source;
    notice.destructive = confirm_label != NULL && destructive;
    notice.surface_brush = CreateSolidBrush(FSV_COLOR_SURFACE);
    if (notice.surface_brush == NULL) {
        goto cleanup;
    }
    embedded_font = fsv_register_embedded_ui_font();
    window = CreateWindowExW(owner != NULL ? WS_EX_TOOLWINDOW : WS_EX_APPWINDOW, NOTICE_CLASS_NAME, title,
        WS_POPUP | WS_CLIPCHILDREN, max(anchor.left, info.rcWork.left), max(anchor.top, info.rcWork.top),
        1, 1, owner, NULL, instance, &notice);
    if (window == NULL) {
        goto cleanup;
    }
    notice.dpi = GetDpiForWindow(window);
    if (notice.dpi == 0) {
        notice.dpi = 96;
    }
    notice.body_edit = CreateWindowExW(0, L"EDIT", body,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL,
        0, 0, 1, 1, window, NULL, instance, NULL);
    notice.dismiss = notice_button(window, IDC_NOTICE_DISMISS, confirm_label != NULL ? L"取消" : L"关闭", &notice, instance);
    if (confirm_label != NULL) {
        notice.confirm = notice_button(window, IDC_NOTICE_CONFIRM, confirm_label, &notice, instance);
    }
    notice.close = notice_button(window, IDC_NOTICE_CLOSE, confirm_label != NULL ? L"取消" : L"关闭", &notice, instance);
    if (notice.body_edit == NULL || notice.dismiss == NULL || notice.close == NULL ||
        (confirm_label != NULL && notice.confirm == NULL) || !notice_layout(window, &notice, &anchor, &info.rcWork)) {
        goto cleanup;
    }

    if (owner != NULL) {
        EnableWindow(owner, FALSE);
    }
    ShowWindow(window, SW_SHOW);
    SetFocus(notice.dismiss);
    while (notice.result == NOTICE_UNAVAILABLE && IsWindow(window) &&
           (status = GetMessageW(&message, NULL, 0, 0)) > 0) {
        if (message.hwnd == window || IsChild(window, message.hwnd)) {
            /* Return keeps the dismiss default even if the confirm button has
               focus. Space still deliberately activates the focused button. */
            if ((message.message == WM_KEYDOWN && (message.wParam == VK_RETURN || message.wParam == VK_ESCAPE)) ||
                (message.message == WM_SYSKEYDOWN && message.wParam == VK_F4)) {
                notice.result = NOTICE_DISMISSED;
                break;
            }
            if (message.message == WM_KEYDOWN && message.wParam == VK_TAB) {
                SetFocus(GetNextDlgTabItem(window, GetFocus(), GetKeyState(VK_SHIFT) < 0));
                continue;
            }
        }
        /* Ignore queued interaction for the disabled owner. Continue pumping
           its paints and application messages while the owned notice is open. */
        if (owner != NULL && (message.hwnd == owner || IsChild(owner, message.hwnd)) &&
            message.message == WM_COMMAND) {
            continue;
        }
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    if (IsWindow(owner)) {
        BOOL restore_focus = GetActiveWindow() == window;
        EnableWindow(owner, TRUE);
        if (restore_focus) {
            SetActiveWindow(owner);
            if (IsChild(owner, previous_focus)) {
                SetFocus(previous_focus);
            }
        }
    }
    result = notice.result == NOTICE_CONFIRMED ? NOTICE_CONFIRMED : NOTICE_DISMISSED;

cleanup:
    if (window != NULL && IsWindow(window)) {
        DestroyWindow(window);
    }
    DeleteObject(notice.surface_brush);
    DeleteObject(notice.title_font);
    DeleteObject(notice.body_font);
    DeleteObject(notice.action_font);
    DeleteObject(notice.source_font);
    if (embedded_font != NULL) {
        RemoveFontMemResourceEx(embedded_font);
    }
    if (prior_dpi != NULL) {
        SetThreadDpiAwarenessContext(prior_dpi);
    }
    if (status == 0) {
        /* Leave the caller's message loop responsible for shutting down. */
        PostQuitMessage((int)message.wParam);
    }
    return result;
}

BOOL fsv_notice_show(HWND owner, const wchar_t *title, const wchar_t *body,
                     const wchar_t *confirm_label, BOOL destructive) {
    FsvNoticeResult result = notice_run(owner, title, body, confirm_label, destructive, NULL);
    if (result == NOTICE_UNAVAILABLE && confirm_label != NULL) {
        fsv_notice_error(owner, title, L"无法显示确认窗口，操作已取消。");
    }
    return result == NOTICE_CONFIRMED;
}

BOOL fsv_notice_message(HWND owner, const wchar_t *title, const wchar_t *body) {
    FsvNoticeResult result = notice_run(owner, title, body, NULL, FALSE, L"操作失败");
    if (result == NOTICE_UNAVAILABLE) {
        HWND fallback_owner = owner != NULL && IsWindowEnabled(owner) ? owner : NULL;
        MessageBoxW(fallback_owner, body, title, MB_OK | MB_ICONERROR);
        return FALSE;
    }
    return TRUE;
}

void fsv_notice_error(HWND owner, const wchar_t *title, const wchar_t *body) {
    (void)fsv_notice_message(owner, title, body);
}

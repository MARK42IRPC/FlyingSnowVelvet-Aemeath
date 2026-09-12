#define wWinMain uninstaller_entry
#include "uninstaller.c"
#undef wWinMain

/* The harness exercises layout and painting only; no file system cleanup runs. */

static BOOL save_bitmap(const wchar_t *path, HDC dc, HBITMAP bitmap, int width, int height) {
    BITMAPFILEHEADER header;
    BITMAPINFO info;
    DWORD bytes = (DWORD)(width * height * 4);
    BYTE *pixels = (BYTE *)HeapAlloc(GetProcessHeap(), 0, bytes);
    HANDLE file;
    DWORD written;
    BOOL result = FALSE;
    ZeroMemory(&info, sizeof(info));
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    if (pixels == NULL || !GetDIBits(dc, bitmap, 0, (UINT)height, pixels, &info, DIB_RGB_COLORS)) goto cleanup;
    ZeroMemory(&header, sizeof(header));
    header.bfType = 0x4D42;
    header.bfOffBits = sizeof(header) + sizeof(BITMAPINFOHEADER);
    header.bfSize = header.bfOffBits + bytes;
    file = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) goto cleanup;
    result = WriteFile(file, &header, sizeof(header), &written, NULL)
        && WriteFile(file, &info.bmiHeader, sizeof(BITMAPINFOHEADER), &written, NULL)
        && WriteFile(file, pixels, bytes, &written, NULL);
    CloseHandle(file);
cleanup:
    HeapFree(GetProcessHeap(), 0, pixels);
    return result;
}

static BOOL check_controls(void) {
    HWND child;
    HDC dc = GetDC(g_window);
    RECT client;
    int failures = 0;
    int index;
    wchar_t face[LF_FACESIZE];
    HGDIOBJ old_font = SelectObject(dc, g_body_font);
    GetTextFaceW(dc, ARRAYSIZE(face), face);
    if (wcscmp(face, L"HarmonyOS Sans SC") != 0) {
        fwprintf(stderr, L"Font fallback: %ls\n", face);
        ++failures;
    }
    SelectObject(dc, old_font);
    GetClientRect(g_window, &client);
    for (child = GetWindow(g_window, GW_CHILD); child != NULL; child = GetWindow(child, GW_HWNDNEXT)) {
        RECT bounds;
        RECT measured;
        wchar_t text[1024];
        wchar_t class_name[64];
        LONG_PTR style = GetWindowLongPtrW(child, GWL_STYLE);
        int id = GetDlgCtrlID(child);
        GetClassNameW(child, class_name, ARRAYSIZE(class_name));
        if (!(style & WS_VISIBLE) || wcscmp(class_name, L"STATIC") != 0) {
            continue;
        }
        GetWindowRect(child, &bounds);
        MapWindowPoints(NULL, g_window, (POINT *)&bounds, 2);
        if (bounds.left < 0 || bounds.top < 0 || bounds.right > client.right || bounds.bottom > client.bottom) {
            fprintf(stderr, "Control %d outside client\n", id);
            ++failures;
        }
        GetWindowTextW(child, text, ARRAYSIZE(text));
        old_font = SelectObject(dc, (HFONT)SendMessageW(child, WM_GETFONT, 0, 0));
        measured = bounds;
        DrawTextW(dc, text, -1, &measured, DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX);
        if (measured.bottom > bounds.bottom) {
            fwprintf(stderr, L"Control %d text clipped: %ls\n", id, text);
            ++failures;
        }
        SelectObject(dc, old_font);
    }
    for (index = 0; index < 2; ++index) {
        int id = index == 0 ? IDC_DELETE_VOICE : IDC_DELETE_DATA;
        HWND control = GetDlgItem(g_window, id);
        LONG_PTR style = control == NULL ? 0 : GetWindowLongPtrW(control, GWL_STYLE);
        LRESULT before;
        /* The options are owner drawn: a native BS_AUTOCHECKBOX repaints its own
           glyph over the custom box as soon as the user clicks it. */
        if (control == NULL || (style & BS_TYPEMASK) != BS_OWNERDRAW) {
            fprintf(stderr, "Checkbox %d is not owner drawn\n", id);
            ++failures;
            continue;
        }
        before = SendMessageW(control, BM_GETCHECK, 0, 0);
        SendMessageW(control, BM_SETCHECK, BST_CHECKED, 0);
        if (SendMessageW(control, BM_GETCHECK, 0, 0) != BST_CHECKED) {
            fprintf(stderr, "Checkbox %d lost its checked state\n", id);
            ++failures;
        }
        SendMessageW(control, BM_SETCHECK, BST_UNCHECKED, 0);
        if (SendMessageW(control, BM_GETCHECK, 0, 0) != BST_UNCHECKED) {
            fprintf(stderr, "Checkbox %d kept its checked state\n", id);
            ++failures;
        }
        SendMessageW(control, BM_SETCHECK, (WPARAM)before, 0);
    }
    ReleaseDC(g_window, dc);
    return failures == 0;
}

static void cleanup_uninstaller_ui(void) {
    if (g_window != NULL) {
        DestroyWindow(g_window);
        g_window = NULL;
    }
    if (g_title_font != NULL) DeleteObject(g_title_font);
    if (g_body_font != NULL) DeleteObject(g_body_font);
    if (g_meta_font != NULL) DeleteObject(g_meta_font);
    if (g_embedded_font != NULL) RemoveFontMemResourceEx(g_embedded_font);
    if (g_canvas_brush != NULL) DeleteObject(g_canvas_brush);
    if (g_surface_brush != NULL) DeleteObject(g_surface_brush);
    if (g_raised_brush != NULL) DeleteObject(g_raised_brush);
    CoUninitialize();
}

static void paint_capture_children(HDC dc) {
    HWND child;
    for (child = GetWindow(g_window, GW_CHILD); child != NULL; child = GetWindow(child, GW_HWNDNEXT)) {
        RECT bounds;
        RECT local;
        wchar_t class_name[32];
        LONG_PTR style;
        int saved_dc;
        int id;
        if (!(GetWindowLongPtrW(child, GWL_STYLE) & WS_VISIBLE)) continue;
        GetWindowRect(child, &bounds);
        MapWindowPoints(NULL, g_window, (POINT *)&bounds, 2);
        saved_dc = SaveDC(dc);
        IntersectClipRect(dc, bounds.left, bounds.top, bounds.right, bounds.bottom);
        SetViewportOrgEx(dc, bounds.left, bounds.top, NULL);
        local.left = 0;
        local.top = 0;
        local.right = bounds.right - bounds.left;
        local.bottom = bounds.bottom - bounds.top;
        GetClassNameW(child, class_name, ARRAYSIZE(class_name));
        style = GetWindowLongPtrW(child, GWL_STYLE);
        id = GetDlgCtrlID(child);
        if (wcscmp(class_name, L"Button") == 0 && (id == IDC_DELETE_VOICE || id == IDC_DELETE_DATA)) {
            draw_checkbox(child, dc, id == IDC_DELETE_VOICE ? g_hover_voice : g_hover_data, checkbox_checked(id));
        } else if (wcscmp(class_name, L"Button") == 0 && (style & BS_TYPEMASK) == BS_OWNERDRAW) {
            DRAWITEMSTRUCT item;
            ZeroMemory(&item, sizeof(item));
            item.CtlType = ODT_BUTTON;
            item.CtlID = (UINT)id;
            item.itemID = item.CtlID;
            item.itemAction = ODA_DRAWENTIRE;
            item.itemState = IsWindowEnabled(child) ? 0 : ODS_DISABLED;
            if (GetFocus() == child) item.itemState |= ODS_FOCUS;
            item.hwndItem = child;
            item.hDC = dc;
            item.rcItem = local;
            draw_button(&item);
        } else {
            SendMessageW(child, WM_PRINT, (WPARAM)dc, PRF_CLIENT | PRF_NONCLIENT | PRF_ERASEBKGND);
        }
        RestoreDC(dc, saved_dc);
    }
}

int wmain(int argc, wchar_t **argv) {
    INITCOMMONCONTROLSEX controls = {sizeof(controls), ICC_PROGRESS_CLASS};
    HDC screen;
    HDC dc;
    HBITMAP bitmap;
    HGDIOBJ previous;
    int page;
    BOOL saved;
    if (argc != 4) return 2;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    InitCommonControlsEx(&controls);
    g_dpi = (UINT)_wtoi(argv[2]);
    page = _wtoi(argv[3]);
    if (page == 4) {
        g_cleanup_mode = TRUE;
    }
    StringCchCopyW(g_cleanup.install_root, ARRAYSIZE(g_cleanup.install_root), L"C:\\Program Files\\FlyingSnowVelvet");
    if (!initialize_ui()) {
        return 3;
    }
    if (page == 2) {
        SendMessageW(g_voice_check, BM_SETCHECK, BST_CHECKED, 0);
        SendMessageW(g_data_check, BM_SETCHECK, BST_CHECKED, 0);
    } else if (page == 3) {
        g_hover_action = 100;
        g_hover_voice = 100;
    }
    if (!check_controls()) {
        cleanup_uninstaller_ui();
        return 4;
    }
    ShowWindow(g_window, SW_SHOW);
    UpdateWindow(g_window);
    screen = GetDC(NULL);
    dc = CreateCompatibleDC(screen);
    bitmap = CreateCompatibleBitmap(screen, ui_px(FSV_CLIENT_WIDTH), ui_px(FSV_CLIENT_HEIGHT));
    previous = SelectObject(dc, bitmap);
    SendMessageW(g_window, WM_PRINT, (WPARAM)dc, PRF_CLIENT | PRF_ERASEBKGND);
    paint_capture_children(dc);
    SelectObject(dc, previous);
    saved = save_bitmap(argv[1], dc, bitmap, ui_px(FSV_CLIENT_WIDTH), ui_px(FSV_CLIENT_HEIGHT));
    DeleteObject(bitmap);
    DeleteDC(dc);
    ReleaseDC(NULL, screen);
    cleanup_uninstaller_ui();
    return saved ? 0 : 5;
}

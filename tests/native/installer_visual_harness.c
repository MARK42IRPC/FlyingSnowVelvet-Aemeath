#define wWinMain installer_entry
#include "main.c"
#undef wWinMain

static BOOL g_capture_pressed;

/* The harness exercises layout and painting only; archive extraction is out of scope. */
BOOL fsv_extract_zip(const wchar_t *archive_path, const wchar_t *destination,
                     FsvZipProgressCallback callback) {
    (void)archive_path;
    (void)destination;
    (void)callback;
    SetLastError(ERROR_CALL_NOT_IMPLEMENTED);
    return FALSE;
}

BOOL fsv_zip_get_statistics(const wchar_t *archive_path,
                            ULONGLONG *total_files, ULONGLONG *total_bytes) {
    (void)archive_path;
    if (total_files != NULL) *total_files = 0;
    if (total_bytes != NULL) *total_bytes = 0;
    SetLastError(ERROR_CALL_NOT_IMPLEMENTED);
    return FALSE;
}

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
        wchar_t class_name[32];
        LONG_PTR style = GetWindowLongPtrW(child, GWL_STYLE);
        if (!(style & WS_VISIBLE) || child == g_icon || child == g_progress) continue;
        GetWindowRect(child, &bounds);
        MapWindowPoints(NULL, g_window, (POINT *)&bounds, 2);
        if (bounds.left < 0 || bounds.top < 0 || bounds.right > client.right || bounds.bottom > client.bottom) {
            fprintf(stderr, "Control %d outside client\n", GetDlgCtrlID(child));
            ++failures;
        }
        GetClassNameW(child, class_name, ARRAYSIZE(class_name));
        if (wcscmp(class_name, L"Edit") == 0 || child == g_current_file) continue;
        GetWindowTextW(child, text, ARRAYSIZE(text));
        old_font = SelectObject(dc, (HFONT)SendMessageW(child, WM_GETFONT, 0, 0));
        measured = bounds;
        DrawTextW(dc, text, -1, &measured, DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX);
        if (measured.right > bounds.right || measured.bottom > bounds.bottom) {
            fwprintf(stderr, L"Control %d text clipped: %ls\n", GetDlgCtrlID(child), text);
            ++failures;
        }
        SelectObject(dc, old_font);
    }
    ReleaseDC(g_window, dc);
    return failures == 0;
}

static void cleanup_installer_ui(void) {
    if (g_window != NULL) {
        DestroyWindow(g_window);
        g_window = NULL;
    }
    if (g_heading_font != NULL) DeleteObject(g_heading_font);
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
        if (wcscmp(class_name, L"Button") == 0 && (style & BS_OWNERDRAW) != 0) {
            DRAWITEMSTRUCT item;
            ZeroMemory(&item, sizeof(item));
            item.CtlType = ODT_BUTTON;
            item.CtlID = (UINT)GetDlgCtrlID(child);
            item.itemID = item.CtlID;
            item.itemAction = ODA_DRAWENTIRE;
            item.itemState = IsWindowEnabled(child) ? 0 : ODS_DISABLED;
            if (GetFocus() == child) item.itemState |= ODS_FOCUS;
            if (GetDlgCtrlID(child) == IDC_NEXT && g_capture_pressed) item.itemState |= ODS_SELECTED;
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
    g_capture_pressed = FALSE;
    if (argc != 4) return 2;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    InitCommonControlsEx(&controls);
    g_dpi = (UINT)_wtoi(argv[2]);
    page = _wtoi(argv[3]);
    g_canvas_brush = CreateSolidBrush(FSV_COLOR_CANVAS);
    g_surface_brush = CreateSolidBrush(FSV_COLOR_SURFACE);
    g_raised_brush = CreateSolidBrush(FSV_COLOR_SURFACE_RAISED);
    StringCchCopyW(g_context.install_directory, ARRAYSIZE(g_context.install_directory), L"C:\\Program Files\\FlyingSnowVelvet");
    if (!create_wizard_window() || !create_controls()) {
        cleanup_installer_ui();
        return 3;
    }
    if (page == 2) {
        SetWindowTextW(g_space_values[0], L"3.8 GB");
        SetWindowTextW(g_space_values[1], L"5.6 GB");
        SetWindowTextW(g_space_values[2], L"128.4 GB");
        SetWindowTextW(g_space_info, L"共 18426 个文件。安装后自动释放临时空间。");
    } else if (page == 3) {
        FsvProgressMessage progress;
        ZeroMemory(&progress, sizeof(progress));
        progress.phase = FSV_PHASE_EXTRACTING;
        progress.percent = 64;
        progress.completed_files = 12048;
        progress.total_files = 18426;
        progress.completed_bytes = 2684354560ULL;
        progress.total_bytes = 4080218931ULL;
        progress.eta_known = TRUE;
        progress.eta_seconds = 42;
        StringCchCopyW(progress.current_file, ARRAYSIZE(progress.current_file), L"runtime\\python311\\Lib\\site-packages\\PyQt5\\Qt5\\bin\\Qt5Core.dll");
        format_progress_message(&progress);
        SetWindowTextW(g_status, L"正在解压程序文件");
    } else if (page == 4) {
        g_context.installed = TRUE;
        SetWindowTextW(g_done_text, L"程序文件已安装并通过校验。\r\n\r\n启动飞行雪绒，即可开启桌面陪伴。");
    } else if (page == 5) {
        SetWindowTextW(g_done_title, L"文件校验未通过");
        SetWindowTextW(g_done_text, L"安装包可能损坏或下载不完整。\r\n\r\n请重新下载安装包后再次运行。\r\n错误代码：23（数据错误，循环冗余检查）。");
        SetWindowTextW(g_finish_button, L"退出安装器");
    } else if (page == 6) {
        SetWindowTextW(g_path_edit, L"D:\\测试 & 安装 ! 目录\\很长的自定义路径\\个人程序\\桌面软件\\飞行雪绒\\FlyingSnowVelvet");
    }
    set_page(page == 5 ? 4 : page == 6 ? 1 : page);
    KillTimer(g_window, FSV_FADE_TIMER);
    if (page == 7) {
        set_page(2);
        SetWindowTextW(g_space_values[0], L"3.8 GB");
        SetWindowTextW(g_space_values[1], L"5.6 GB");
        SetWindowTextW(g_space_values[2], L"1.2 GB");
        SetWindowTextW(g_space_info, L"磁盘空间不足，请返回并更换位置。");
        EnableWindow(g_start_button, FALSE);
    }
    if (page == 8 || page == 9) {
        set_page(1);
        if (page == 8) g_button_states[1].hover_amount = 100;
        if (page == 9) g_capture_pressed = TRUE;
    }
    if (!check_controls()) {
        cleanup_installer_ui();
        return 4;
    }
    ShowWindow(g_window, SW_SHOW);
    UpdateWindow(g_window);
    screen = GetDC(NULL);
    dc = CreateCompatibleDC(screen);
    bitmap = CreateCompatibleBitmap(screen, ui_px(FSV_CLIENT_WIDTH), ui_px(FSV_CLIENT_HEIGHT));
    previous = SelectObject(dc, bitmap);
    /* Paint the client first, then each child in client coordinates.  WM_PRINT's
       automatic PRF_CHILDREN path uses the top-level non-client origin on some
       Windows builds and shifts controls below the captured client bitmap. */
    SendMessageW(g_window, WM_PRINT, (WPARAM)dc, PRF_CLIENT | PRF_ERASEBKGND);
    paint_capture_children(dc);
    SelectObject(dc, previous);
    saved = save_bitmap(argv[1], dc, bitmap, ui_px(FSV_CLIENT_WIDTH), ui_px(FSV_CLIENT_HEIGHT));
    DeleteObject(bitmap);
    DeleteDC(dc);
    ReleaseDC(NULL, screen);
    cleanup_installer_ui();
    return saved ? 0 : 5;
}

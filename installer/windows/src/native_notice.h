#pragma once

#include <windows.h>

/* Register the executable's private UI font. Release a non-NULL handle
   with RemoveFontMemResourceEx after destroying the fonts that use it. */
HANDLE fsv_register_embedded_ui_font(void);

/* A non-NULL confirm_label requires an enabled owner on this thread. Only
   explicit activation of that button succeeds. Creation failure is a rejection. */
BOOL fsv_notice_show(HWND owner, const wchar_t *title, const wchar_t *body,
                     const wchar_t *confirm_label, BOOL destructive);

/* These use the same shell with a one-button system fallback when the
   custom host cannot start. The owner can be NULL for early startup. */
void fsv_notice_error(HWND owner, const wchar_t *title, const wchar_t *body);
BOOL fsv_notice_message(HWND owner, const wchar_t *title, const wchar_t *body);

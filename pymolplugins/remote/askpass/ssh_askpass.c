/* Native OpenSSH askpass bridge: Unicode argv, no shell, inherited stdout. */
#include <windows.h>
#include <wchar.h>
#include <stdlib.h>

static wchar_t *append_arg(wchar_t *out, const wchar_t *arg) {
    *out++ = L'"';
    while (*arg) {
        size_t slashes = 0;
        while (*arg == L'\\') { slashes++; arg++; }
        if (*arg == L'"' || !*arg) {
            for (size_t i = 0; i < slashes * 2; i++) *out++ = L'\\';
            if (*arg == L'"') *out++ = L'\\';
        } else {
            for (size_t i = 0; i < slashes; i++) *out++ = L'\\';
        }
        if (*arg) *out++ = *arg++;
    }
    *out++ = L'"';
    *out = 0;
    return out;
}

int wmain(int argc, wchar_t **argv) {
    const wchar_t *python = _wgetenv(L"PYMOLPLUGINS_OTP_PYTHON");
    const wchar_t *helper = _wgetenv(L"PYMOLPLUGINS_OTP_HELPER");
    if (!python || !helper || argc != 2) return 1;
    size_t size = 2 * (wcslen(python) + wcslen(helper) + wcslen(argv[1])) + 16;
    wchar_t *command = calloc(size, sizeof(wchar_t));
    if (!command) return 1;
    wchar_t *out = append_arg(command, python);
    *out++ = L' '; out = append_arg(out, helper);
    *out++ = L' '; append_arg(out, argv[1]);
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    BOOL created = CreateProcessW(python, command, NULL, NULL, TRUE,
                                  CREATE_NO_WINDOW, NULL, NULL, &startup, &process);
    free(command);
    if (!created) return 1;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 1;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}

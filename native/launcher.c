/* Minimal Windows x64 launcher for the private, pinned CPython runtime.
 * No Python installation, shell command, PATH lookup or administrator manifest.
 * The Python bootstrap verifies all native files before importing Qt.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>

typedef void (*initialize_fn)(void);
typedef void (*program_fn)(const wchar_t *);
typedef void (*argv_fn)(int, wchar_t **, int);
typedef int (*run_fn)(const char *);
typedef void *(*module_fn)(const char *);
typedef void *(*dict_fn)(void *);
typedef void *(*item_fn)(void *, const char *);
typedef long (*long_fn)(void *);
typedef int (*finalize_fn)(void);

static void failed(void) {
    MessageBoxW(0, L"SenorX99 could not start its private runtime. Extract the complete ZIP and keep all files together.", L"SenorX99", MB_OK | MB_ICONERROR);
    ExitProcess(2);
}

void entry(void) {
    static wchar_t exe[32768], root[32768], dll[32768];
    DWORD length = GetModuleFileNameW(0, exe, 32768);
    if (!length || length >= 32600) failed();
    lstrcpyW(root, exe);
    while (length && root[length - 1] != L'\\') length--;
    if (!length) failed();
    root[length] = 0;
    lstrcatW(root, L"_runtime");
    if (!SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS)) failed();
    if (!AddDllDirectory(root)) failed();
    lstrcpyW(dll, root); lstrcatW(dll, L"\\python313.dll");
    HMODULE python = LoadLibraryExW(dll, 0, LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (!python) failed();
    initialize_fn initialize = (initialize_fn)GetProcAddress(python, "Py_Initialize");
    program_fn program = (program_fn)GetProcAddress(python, "Py_SetProgramName");
    argv_fn setargv = (argv_fn)GetProcAddress(python, "PySys_SetArgvEx");
    run_fn run = (run_fn)GetProcAddress(python, "PyRun_SimpleString");
    module_fn module = (module_fn)GetProcAddress(python, "PyImport_AddModule");
    dict_fn dictionary = (dict_fn)GetProcAddress(python, "PyModule_GetDict");
    item_fn item = (item_fn)GetProcAddress(python, "PyDict_GetItemString");
    long_fn number = (long_fn)GetProcAddress(python, "PyLong_AsLong");
    finalize_fn finalize = (finalize_fn)GetProcAddress(python, "Py_FinalizeEx");
    if (!initialize || !program || !setargv || !run || !module || !dictionary || !item || !number || !finalize) failed();
    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) failed();
    program(exe); initialize(); setargv(argc, argv, 0);
#ifdef SENOR_CLI
    const char *kind = "import sys, os\nsys.frozen = True\nsys._senor_native_launcher = True\nsys._senor_kind = 'cli'\nsys._MEIPASS = os.path.join(os.path.dirname(sys.executable), '_runtime')\nfrom x99toolkit.runtime_bootstrap import run\n_senor_exit = run('cli')\n";
#else
    const char *kind = "import sys, os\nsys.frozen = True\nsys._senor_native_launcher = True\nsys._senor_kind = 'gui'\nsys._MEIPASS = os.path.join(os.path.dirname(sys.executable), '_runtime')\nfrom x99toolkit.runtime_bootstrap import run\n_senor_exit = run('gui')\n";
#endif
    int status = run(kind);
    long result = 2;
    if (!status) {
        void *value = item(dictionary(module("__main__")), "_senor_exit");
        if (value) result = number(value);
    }
    if (finalize() < 0 && result == 0) result = 120;
    LocalFree(argv);
    ExitProcess((UINT)result);
}

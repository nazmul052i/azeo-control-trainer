// A small GUI launcher keeps file associations, PATH and installed Python out
// of a portable launch. /MT lets even a machine without the VC runtime explain
// an incomplete extraction before starting the bundled interpreter.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <filesystem>
#include <string>
#include <vector>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR arguments, int) {
    std::vector<wchar_t> filename(32768);
    const auto length = GetModuleFileNameW(nullptr, filename.data(), DWORD(filename.size()));
    if (!length || length >= filename.size()) return 1;
    const std::filesystem::path executable(filename.data());
    const auto root = executable.parent_path();
    // Setup waits for the controller and its audit workers to close normally.
    const auto running = CreateMutexW(nullptr, FALSE, L"Local\\AzeoSuite.Running");
    const auto python = root / L"runtime" / L"pythonw.exe";
    const auto script = root / L"_azeo_launch.py";
    if (!std::filesystem::exists(python) || !std::filesystem::exists(script)) {
        MessageBoxW(nullptr, L"Extract the entire Azeo Windows ZIP into one folder, then open Azeo.exe.\n"
                    L"The runtime folder and application files must remain beside the launcher.",
                    L"Azeo application files are missing", MB_OK | MB_ICONERROR);
        return 2;
    }
    std::wstring mode;
    const auto name = executable.filename().wstring();
    if (name == L"AzeoSimulator.exe") mode = L" --simulator";
    else if (name == L"AzeoControlDesigner.exe") mode = L" --classic";
    else if (name == L"AzeoGraphicsDesigner.exe") mode = L" --graphics";
    else if (name == L"AzeoOperatorStation.exe") mode = L" --station";
    else if (name == L"AzeoSimulationWorkbench.exe") mode = L" --simulation";
    else if (name == L"AzeoPADesigner.exe") mode = L" --procedures";
    else if (name == L"AzeoHelp.exe") mode = L" --help-center";
    // wWinMain already receives the correctly quoted argument tail. Keeping
    // it intact preserves Unicode project paths and trailing backslashes.
    std::wstring command = L"\"" + python.wstring() + L"\" -B \"" + script.wstring()
                         + L"\"" + mode + L" " + arguments;
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    if (!CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, nullptr, &startup, &process)) {
        wchar_t reason[512]{};
        const auto code = GetLastError();
        FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
                       nullptr, code, 0, reason, 512, nullptr);
        const std::wstring message = L"Azeo could not start its bundled runtime.\nWindows error "
                                   + std::to_wstring(code) + L": " + reason;
        MessageBoxW(nullptr, message.c_str(), L"Azeo could not start", MB_OK | MB_ICONERROR);
        return 3;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD result = 1;
    GetExitCodeProcess(process.hProcess, &result);
    CloseHandle(process.hProcess);
    if (running) CloseHandle(running);
    return int(result);
}

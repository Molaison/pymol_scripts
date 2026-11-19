# Run the Plugin Manager Bundle regression on a non-input Windows desktop.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [string]$HostPython = 'D:\Softwares\Pymol3.1\pythonw.exe'
)
$ErrorActionPreference = 'Stop'
$packagePath = (Resolve-Path -LiteralPath $Package).ProviderPath
$pythonPath = (Resolve-Path -LiteralPath $HostPython).ProviderPath
$smoke = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\tests\host\plugin_package_smoke.py')).ProviderPath
$output = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputPath)
if (Test-Path -LiteralPath $output) { throw "Use a new evidence path: $output" }
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class BundleDesktop {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct Startup {
        public int cb;
        public string reserved, desktop, title;
        public int x, y, width, height, columns, rows, fill, flags;
        public short show, reservedLength;
        public IntPtr reservedPointer, stdin, stdout, stderr;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct Process { public IntPtr process, thread; public uint pid, tid; }
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern IntPtr CreateDesktop(string name, IntPtr device, IntPtr mode, uint flags, uint access, IntPtr security);
    [DllImport("user32.dll", SetLastError=true)]
    static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder name, int bytes, out int needed);
    [DllImport("user32.dll", SetLastError=true)] public static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool CreateProcess(string exe, StringBuilder args, IntPtr ps, IntPtr ts, bool inherit,
        uint flags, IntPtr env, string cwd, ref Startup startup, out Process process);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern uint WaitForSingleObject(IntPtr process, uint ms);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll", SetLastError=true)] public static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
    public static void Check(bool ok, string operation) {
        if (!ok) throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
    }
    public static string InputDesktop() {
        var input = OpenInputDesktop(0, false, 1);
        Check(input != IntPtr.Zero, "OpenInputDesktop in the interactive session");
        try {
            var name = new StringBuilder(256);
            int needed;
            Check(GetUserObjectInformation(input, 2, name, name.Capacity * 2, out needed), "Read input desktop name");
            return name.ToString();
        } finally { CloseDesktop(input); }
    }
}
'@
$inputBefore = [BundleDesktop]::InputDesktop()
$name = 'PymolBundleSmoke-' + [Guid]::NewGuid().ToString('N')
$desktop = [BundleDesktop]::CreateDesktop($name, [IntPtr]::Zero, [IntPtr]::Zero, 0, 0xff, [IntPtr]::Zero)
[BundleDesktop]::Check(($desktop -ne [IntPtr]::Zero), 'CreateDesktop')
$previousDesktop = $env:PP_BUNDLE_SMOKE_DESKTOP
$previousPath = $env:PATH
try {
    if ([BundleDesktop]::InputDesktop() -eq $name) { throw 'Test desktop is the input desktop' }
    $env:PP_BUNDLE_SMOKE_DESKTOP = $name
    $pythonDir = Split-Path -Parent $pythonPath
    $env:PATH = "$pythonDir;$pythonDir\Library\bin;$previousPath"
    $startup = New-Object BundleDesktop+Startup
    $startup.cb = [Runtime.InteropServices.Marshal]::SizeOf($startup)
    $startup.desktop = 'WinSta0\' + $name
    $child = New-Object BundleDesktop+Process
    $arguments = '"{0}" -I -u "{1}" "{2}" --bundle-only --output "{3}"' -f $pythonPath, $smoke, $packagePath, $output
    $commandLine = [Text.StringBuilder]::new($arguments)
    [BundleDesktop]::Check([BundleDesktop]::CreateProcess($pythonPath, $commandLine, [IntPtr]::Zero, [IntPtr]::Zero,
        $false, 0x08000000, [IntPtr]::Zero, [IO.Path]::GetTempPath(), [ref]$startup, [ref]$child), 'Start hidden PyMOL')
    try {
        Write-Output "inputDesktop=$inputBefore testDesktop=$name pid=$($child.pid)"
        $wait = [BundleDesktop]::WaitForSingleObject($child.process, 55000)
        if ($wait -eq 258) { $wait = [BundleDesktop]::WaitForSingleObject($child.process, 55000) }
        if ($wait -eq 258) {
            [BundleDesktop]::Check([BundleDesktop]::TerminateProcess($child.process, 124), 'Stop timed-out test process')
            [void][BundleDesktop]::WaitForSingleObject($child.process, 3000)
            throw "Timed out waiting for PyMOL Bundle evidence: $output"
        }
        [BundleDesktop]::Check(($wait -eq 0), 'Wait for hidden PyMOL')
        $code = [uint32]0
        [BundleDesktop]::Check([BundleDesktop]::GetExitCodeProcess($child.process, [ref]$code), 'Read PyMOL exit code')
    } finally {
        [void][BundleDesktop]::CloseHandle($child.thread)
        [void][BundleDesktop]::CloseHandle($child.process)
    }
    if (([BundleDesktop]::InputDesktop() -eq $name) -or !(Test-Path -LiteralPath $output)) {
        throw "No hidden-desktop Bundle evidence. PyMOL exit=$code evidence=$output"
    }
    $result = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    if ($code -ne 0 -or $result.status -ne 'passed' -or $result.bundle.desktop -ne $name) {
        throw "Bundle regression failed. PyMOL exit=$code evidence=$output"
    }
    Write-Output "Bundle regression passed. Evidence: $output"
} finally {
    $env:PATH = $previousPath
    $env:PP_BUNDLE_SMOKE_DESKTOP = $previousDesktop
    [void][BundleDesktop]::CloseDesktop($desktop)
}

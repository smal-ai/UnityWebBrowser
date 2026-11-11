// UnityWebBrowser (UWB)
// Copyright (c) 2021-2024 Voltstro-Studios
// 
// This project is under the MIT license. See the LICENSE.md file for more details.

#if UNITY_STANDALONE_WIN

using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace VoltstroStudios.UnityWebBrowser.Core.Engines.Process
{
    internal sealed class WindowProcess : IProcess
    {
        private readonly object gate = new();
        private IntPtr jobHandle;
        private IntPtr processHandle;
        private IntPtr stdoutRead;
        private IntPtr stderrRead;
        private Thread stdoutThread;
        private Thread stderrThread;
        private Exception readerFailure;
        private bool started;
        private bool killRequested;
        private volatile bool disposed;

        public WindowProcess()
        {
            string jobName = "UWBChildProcesses" + Guid.NewGuid().ToString("N");
            jobHandle = CreateJobObject(IntPtr.Zero, jobName);
            if (jobHandle == IntPtr.Zero)
                throw NativeError("CreateJobObject");
            
            JOBOBJECT_BASIC_LIMIT_INFORMATION info = new()
            {
                // This is the key flag. When our process is killed, Windows will automatically
                //  close the job handle, and when that happens, we want the child processes to
                //  be killed, too.
                LimitFlags = JOBOBJECTLIMIT.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            };

            JOBOBJECT_EXTENDED_LIMIT_INFORMATION extendedInfo = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
                {
                    BasicLimitInformation = info
                };

            int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
            IntPtr extendedInfoPtr = Marshal.AllocHGlobal(length);
            try
            {
                Marshal.StructureToPtr(extendedInfo, extendedInfoPtr, false);

                if (!SetInformationJobObject(jobHandle, JobObjectInfoType.ExtendedLimitInformation,
                        extendedInfoPtr, (uint)length))
                {
                    Win32Exception error = NativeError("SetInformationJobObject");
                    Close(ref jobHandle);
                    throw error;
                }
            }
            finally
            {
                Marshal.FreeHGlobal(extendedInfoPtr);
            }
            
        }
        
        public void StartProcess(string executable, string workingDir, string arguments, Action<string> onLogEvent, Action<string> onErrorLogEvent)
        {
            lock (gate)
            {
                if (disposed)
                    throw new ObjectDisposedException(nameof(WindowProcess));
                if (killRequested)
                    throw new OperationCanceledException("The native engine launch was cancelled before startup.");
                if (started)
                    throw new InvalidOperationException("This process instance already started.");
                if (string.IsNullOrEmpty(executable) || executable.Contains('"'))
                    throw new ArgumentException("An explicit Windows executable path is required.", nameof(executable));
                started = true;

                IntPtr stdoutWrite = IntPtr.Zero;
                IntPtr stderrWrite = IntPtr.Zero;
                IntPtr stdinNull = IntPtr.Zero;
                IntPtr attributeList = IntPtr.Zero;
                IntPtr handleList = IntPtr.Zero;
                ProcessInformation child = default;
                bool attributesInitialised = false;
                try
                {
                    SecurityAttributes security = new()
                    {
                        Length = (uint)Marshal.SizeOf<SecurityAttributes>(),
                        InheritHandle = 1
                    };
                    OpenPipe(ref security, out stdoutRead, out stdoutWrite);
                    OpenPipe(ref security, out stderrRead, out stderrWrite);
                    stdinNull = CreateFile("NUL", 0x80000000, 3, ref security, 3, 0x80, IntPtr.Zero);
                    if (stdinNull == new IntPtr(-1))
                    {
                        stdinNull = IntPtr.Zero;
                        throw NativeError("CreateFile(NUL)");
                    }

                    // Inherit only the three stdio handles, not Unity's other handles.
                    UIntPtr attributeBytes = UIntPtr.Zero;
                    InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref attributeBytes);
                    attributeList = Marshal.AllocHGlobal(checked((int)attributeBytes.ToUInt64()));
                    if (!InitializeProcThreadAttributeList(attributeList, 1, 0, ref attributeBytes))
                        throw NativeError("InitializeProcThreadAttributeList");
                    attributesInitialised = true;
                    handleList = Marshal.AllocHGlobal(IntPtr.Size * 3);
                    Marshal.WriteIntPtr(handleList, 0, stdinNull);
                    Marshal.WriteIntPtr(handleList, IntPtr.Size, stdoutWrite);
                    Marshal.WriteIntPtr(handleList, IntPtr.Size * 2, stderrWrite);
                    if (!UpdateProcThreadAttribute(attributeList, 0, new IntPtr(0x00020002), handleList,
                            new UIntPtr((uint)(IntPtr.Size * 3)), IntPtr.Zero, IntPtr.Zero))
                        throw NativeError("UpdateProcThreadAttribute(HANDLE_LIST)");

                    StartupInfoEx info = new()
                    {
                        StartupInfo = new StartupInfo
                        {
                            Size = (uint)Marshal.SizeOf<StartupInfoEx>(),
                            Flags = 0x00000100,
                            StdInput = stdinNull,
                            StdOutput = stdoutWrite,
                            StdError = stderrWrite
                        },
                        AttributeList = attributeList
                    };
                    StringBuilder commandLine = new('"' + executable + "\" " + (arguments ?? ""));
                    // Suspend before assigning the job so early CEF children cannot escape.
                    const uint flags = 0x00000004 | 0x00080000 | 0x08000000;
                    if (!CreateProcess(executable, commandLine, IntPtr.Zero, IntPtr.Zero, true, flags,
                            IntPtr.Zero, workingDir, ref info, out child))
                        throw NativeError("CreateProcessW");
                    processHandle = child.Process;
                    if (!AssignProcessToJobObject(jobHandle, processHandle))
                        throw NativeError("AssignProcessToJobObject");
                    Close(ref stdinNull);
                    Close(ref stdoutWrite);
                    Close(ref stderrWrite);
                    if (ResumeThread(child.Thread) == uint.MaxValue)
                        throw NativeError("ResumeThread");

                    IntPtr output = stdoutRead;
                    IntPtr errorOutput = stderrRead;
                    stdoutThread = new Thread(() => ReadPipe(output, onLogEvent)) { IsBackground = true, Name = "UWB stdout" };
                    stderrThread = new Thread(() => ReadPipe(errorOutput, onErrorLogEvent)) { IsBackground = true, Name = "UWB stderr" };
                    stdoutThread.Start();
                    stderrThread.Start();
                }
                catch
                {
                    if (processHandle != IntPtr.Zero)
                    {
                        TerminateJobObject(jobHandle, 1);
                        TerminateProcess(processHandle, 1);
                        WaitForSingleObject(processHandle, 2000);
                    }
                    Close(ref processHandle);
                    Close(ref stdoutRead);
                    Close(ref stderrRead);
                    throw;
                }
                finally
                {
                    Close(ref child.Thread);
                    Close(ref stdinNull);
                    Close(ref stdoutWrite);
                    Close(ref stderrWrite);
                    if (attributesInitialised)
                        DeleteProcThreadAttributeList(attributeList);
                    if (attributeList != IntPtr.Zero)
                        Marshal.FreeHGlobal(attributeList);
                    if (handleList != IntPtr.Zero)
                        Marshal.FreeHGlobal(handleList);
                }
            }
        }

        public void KillProcess()
        {
            lock (gate)
            {
                if (disposed || killRequested)
                    return;
                if (jobHandle != IntPtr.Zero && !TerminateJobObject(jobHandle, 0))
                    throw NativeError("TerminateJobObject");
                killRequested = true;
            }
        }

        public bool HasExited
        {
            get
            {
                lock (gate)
                {
                    if (processHandle == IntPtr.Zero)
                        return true;
                    uint state = WaitForSingleObject(processHandle, 0);
                    if (state == uint.MaxValue)
                        throw NativeError("WaitForSingleObject");
                    return state == 0;
                }
            }
        }

        public int ExitCode
        {
            get
            {
                lock (gate)
                {
                    if (processHandle == IntPtr.Zero || WaitForSingleObject(processHandle, 0) != 0)
                        throw new InvalidOperationException("The native process has not exited.");
                    if (!GetExitCodeProcess(processHandle, out uint code))
                        throw NativeError("GetExitCodeProcess");
                    return unchecked((int)code);
                }
            }
        }

        public void Dispose()
        {
            IntPtr process;
            lock (gate)
            {
                if (disposed)
                    return;
                disposed = true;
                killRequested = true;
                // Closing the job below remains a second kill-on-close safeguard.
                if (jobHandle != IntPtr.Zero)
                    TerminateJobObject(jobHandle, 0);
                process = processHandle;
            }
            try
            {
                if (process != IntPtr.Zero)
                    WaitForSingleObject(process, 2000);
                Join(stdoutThread);
                Join(stderrThread);
            }
            finally
            {
                lock (gate)
                {
                    Close(ref stdoutRead);
                    Close(ref stderrRead);
                    Close(ref processHandle);
                    Close(ref jobHandle);
                }
            }
            if (readerFailure != null)
                throw new IOException("The native process log reader failed.", readerFailure);
        }

        private void ReadPipe(IntPtr pipe, Action<string> receive)
        {
            byte[] bytes = new byte[4096];
            char[] characters = new char[Encoding.UTF8.GetMaxCharCount(bytes.Length)];
            Decoder decoder = Encoding.UTF8.GetDecoder();
            StringBuilder line = new();
            try
            {
                while (!disposed)
                {
                    if (!ReadFile(pipe, bytes, (uint)bytes.Length, out uint count, IntPtr.Zero))
                    {
                        int error = Marshal.GetLastWin32Error();
                        if (disposed || error is 6 or 109 or 232 or 995)
                            break;
                        throw new Win32Exception(error, "ReadFile(pipe)");
                    }
                    if (count == 0)
                        break;
                    int length = decoder.GetChars(bytes, 0, (int)count, characters, 0, false);
                    for (int index = 0; index < length; index++)
                    {
                        if (characters[index] == '\n')
                        {
                            receive?.Invoke(line.ToString().TrimEnd('\r'));
                            line.Clear();
                        }
                        else
                            line.Append(characters[index]);
                    }
                }
                if (!disposed)
                {
                    int finalCharacters = decoder.GetChars(Array.Empty<byte>(), 0, 0, characters, 0, true);
                    line.Append(characters, 0, finalCharacters);
                    if (line.Length > 0)
                        receive?.Invoke(line.ToString().TrimEnd('\r'));
                    receive?.Invoke(null);
                }
            }
            catch (Exception exception)
            {
                lock (gate)
                    readerFailure ??= exception;
            }
            finally
            {
                lock (gate)
                {
                    if (stdoutRead == pipe)
                        Close(ref stdoutRead);
                    if (stderrRead == pipe)
                        Close(ref stderrRead);
                }
            }
        }

        private static void Join(Thread thread)
        {
            if (thread != null && thread.IsAlive && thread != Thread.CurrentThread)
                thread.Join(2000);
        }

        private static void OpenPipe(ref SecurityAttributes security, out IntPtr read, out IntPtr write)
        {
            if (!CreatePipe(out read, out write, ref security, 0))
                throw NativeError("CreatePipe");
            if (!SetHandleInformation(read, 1, 0))
                throw NativeError("SetHandleInformation");
        }

        private static Win32Exception NativeError(string operation)
        {
            return new Win32Exception(Marshal.GetLastWin32Error(), operation);
        }

        private static void Close(ref IntPtr handle)
        {
            IntPtr value = handle;
            handle = IntPtr.Zero;
            if (value != IntPtr.Zero && value != new IntPtr(-1))
                CloseHandle(value);
        }

        #region WIN32 Native
        
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(IntPtr job, JobObjectInfoType infoType,
            IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

        [DllImport("kernel32.dll", EntryPoint = "CreateProcessW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(string applicationName, StringBuilder commandLine,
            IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint creationFlags,
            IntPtr environment, string currentDirectory, ref StartupInfoEx startupInfo, out ProcessInformation processInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CreatePipe(out IntPtr read, out IntPtr write, ref SecurityAttributes attributes, uint size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);

        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateFile(string name, uint access, uint share, ref SecurityAttributes security,
            uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref UIntPtr size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value,
            UIntPtr size, IntPtr previousValue, IntPtr returnSize);

        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(IntPtr thread);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool ReadFile(IntPtr file, [Out] byte[] buffer, uint size, out uint read, IntPtr overlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetExitCodeProcess(IntPtr process, out uint code);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateProcess(IntPtr process, uint code);

        [DllImport("kernel32.dll")]
        private static extern bool CloseHandle(IntPtr handle);

        [StructLayout(LayoutKind.Sequential)]
        internal struct SecurityAttributes
        {
            public uint Length;
            public IntPtr SecurityDescriptor;
            public int InheritHandle;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct StartupInfo
        {
            public uint Size;
            public IntPtr Reserved;
            public IntPtr Desktop;
            public IntPtr Title;
            public uint X;
            public uint Y;
            public uint XSize;
            public uint YSize;
            public uint XCharacters;
            public uint YCharacters;
            public uint FillAttribute;
            public uint Flags;
            public ushort ShowWindow;
            public ushort ReservedBytes;
            public IntPtr ReservedData;
            public IntPtr StdInput;
            public IntPtr StdOutput;
            public IntPtr StdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        internal struct StartupInfoEx
        {
            public StartupInfo StartupInfo;
            public IntPtr AttributeList;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation
        {
            public IntPtr Process;
            public IntPtr Thread;
            public uint ProcessId;
            public uint ThreadId;
        }

        public enum JobObjectInfoType
        {
            AssociateCompletionPortInformation = 7,
            BasicLimitInformation = 2,
            BasicUIRestrictions = 4,
            EndOfJobTimeInformation = 6,
            ExtendedLimitInformation = 9,
            SecurityLimitInformation = 5,
            GroupInformation = 11
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public Int64 PerProcessUserTimeLimit;
            public Int64 PerJobUserTimeLimit;
            public JOBOBJECTLIMIT LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public UInt32 ActiveProcessLimit;
            public UIntPtr Affinity;
            public UInt32 PriorityClass;
            public UInt32 SchedulingClass;
        }

        [Flags]
        public enum JOBOBJECTLIMIT : uint
        {
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct IO_COUNTERS
        {
            public UInt64 ReadOperationCount;
            public UInt64 WriteOperationCount;
            public UInt64 OtherOperationCount;
            public UInt64 ReadTransferCount;
            public UInt64 WriteTransferCount;
            public UInt64 OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        #endregion
    }
}

#endif

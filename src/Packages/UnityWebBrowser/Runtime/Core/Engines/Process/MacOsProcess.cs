// UnityWebBrowser (UWB)
// Copyright (c) 2021-2024 Voltstro-Studios
// 
// This project is under the MIT license.See the LICENSE.md file for more details.

using System;
using System.Diagnostics;
using VoltstroStudios.UnityWebBrowser.Helper;

#if UNITY_STANDALONE_OSX

namespace VoltstroStudios.UnityWebBrowser.Core.Engines.Process
{
    internal sealed class MacOsProcess : IProcess
    {
        private readonly System.Diagnostics.Process process;
        
        public MacOsProcess()
        {
            process = new System.Diagnostics.Process();
        }

        public void StartProcess(string executable, string workingDir, string arguments, Action<string> onLogEvent,
            Action<string> onErrorLogEvent)
        {
            ProcessStartInfo startInfo = new(executable, arguments)
            {
                CreateNoWindow = true,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                WorkingDirectory = workingDir
            };

            process.StartInfo = startInfo;
            process.OutputDataReceived += (_, entry) => onLogEvent?.Invoke(entry.Data);
            process.ErrorDataReceived += (_, entry) => onErrorLogEvent?.Invoke(entry.Data);
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }

        public void KillProcess()
        {
            process.KillTree();
        }

        public bool HasExited => process.HasExited;
        public int ExitCode => process.ExitCode;
        
        public void Dispose()
        {
            process.Dispose();
        }
    }
}

#endif

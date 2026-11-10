using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using VoltstroStudios.UnityWebBrowser.Core.Engines.Process;

namespace VoltstroStudios.UnityWebBrowser.Tests.WindowsProcessProbe
{
    internal static class Program
    {
        private static string work;
        private static int failures;
        private static readonly string Sentinel = "FIXTURE_STDOUT £é漢字";

        public static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            if (args.Length > 0 && args[0] == "--fixture")
                return Fixture(args.Skip(1).ToArray());
            Check("native_64bit_layout", Layout);
            if (!OperatingSystem.IsWindows())
            {
                Console.WriteLine("{\"check\":\"windows_native_execution\",\"outcome\":\"NOT RUN\",\"detail\":\"ABI sizes only on this host. No Windows process was launched.\"}");
                return failures == 0 ? 0 : 1;
            }
            if (args.Length > 1)
                throw new ArgumentException("Supply at most one new probe output directory.");
            work = Path.GetFullPath(args.Length == 1 ? args[0] : Path.Combine(
                Path.GetTempPath(), "UnityWebBrowser-ProcessProbe", Guid.NewGuid().ToString("N")));
            if (Directory.Exists(work) || File.Exists(work))
                throw new IOException("The probe output path already exists. No adoption or overwrite.");
            Directory.CreateDirectory(work);
            Console.WriteLine("{\"workDirectory\":\"" + Escape(work) + "\"}");
            Check("stdout_stderr_unicode_eof_exit", NaturalExit);
            Check("quoted_unicode_arguments", QuotedArguments);
            Check("controlled_loopback_handshake", Handshake);
            Check("failed_start_safe_dispose", FailedStart);
            Check("cancel_before_start", CancelBeforeStart);
            Check("job_descendant_cleanup_repeat_kill", TreeCleanup);
            Check("natural_parent_exit_descendant_dispose", OrphanCleanup);
            Console.WriteLine("{\"summary\":true,\"failures\":" + failures + "}");
            return failures == 0 ? 0 : 1;
        }

        private static void Layout()
        {
            Require(IntPtr.Size == 8, "This probe validates only 64-bit process layouts.");
            Require(Marshal.SizeOf<WindowProcess.SecurityAttributes>() == 24, "SECURITY_ATTRIBUTES layout is not 24 bytes.");
            Require(Marshal.SizeOf<WindowProcess.StartupInfo>() == 104, "STARTUPINFO layout is not 104 bytes.");
            Require(Marshal.SizeOf<WindowProcess.StartupInfoEx>() == 112, "STARTUPINFOEX layout is not 112 bytes.");
            Require(Marshal.SizeOf<WindowProcess.JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() == 144, "Job limits layout is not 144 bytes.");
        }

        private static void Check(string name, Action check)
        {
            try
            {
                check();
                Result(name, "PASS", "Measured against the linked runtime source.");
            }
            catch (Exception exception)
            {
                failures++;
                Result(name, "FAIL", exception.ToString());
            }
        }

        private static void Result(string name, string outcome, string detail)
        {
            Console.WriteLine("{\"check\":\"" + Escape(name) + "\",\"outcome\":\"" + outcome + "\",\"detail\":\"" + Escape(detail) + "\"}");
        }

        private static string Escape(string value) => value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t");
        private static string Quote(string value) => "\"" + value.Replace("\"", "\\\"") + "\"";
        private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }

        private static bool Wait(Func<bool> condition, int timeout = 5000)
        {
            Stopwatch clock = Stopwatch.StartNew();
            while (clock.ElapsedMilliseconds < timeout)
            {
                if (condition()) return true;
                Thread.Sleep(10);
            }
            return condition();
        }

        private static void NaturalExit()
        {
            using Capture capture = new();
            capture.Start("natural");
            Require(Wait(() => capture.Process.HasExited), "The native fixture did not exit.");
            Require(capture.Process.ExitCode == 17, "Exit code was not 17.");
            capture.Output();
            Require(capture.Stdout.Contains(Sentinel), "Split UTF-8 output was corrupted.");
        }

        private static void QuotedArguments()
        {
            using Capture capture = new();
            capture.Start("argument " + Quote("spaces £é漢字"));
            Require(Wait(() => capture.Process.HasExited), "Argument fixture did not exit.");
            capture.Output();
            Require(capture.Stdout.Contains("ARG=spaces £é漢字"), "Quoted Unicode arguments changed.");
        }

        private static void Handshake()
        {
            TcpListener listener = new(IPAddress.Loopback, 0);
            listener.Start();
            try
            {
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                using Capture capture = new();
                capture.Start("handshake " + port);
                IAsyncResult pending = listener.BeginAcceptTcpClient(null, null);
                Require(pending.AsyncWaitHandle.WaitOne(5000), "Fixture loopback accept timed out.");
                using TcpClient client = listener.EndAcceptTcpClient(pending);
                client.ReceiveTimeout = 5000;
                client.SendTimeout = 5000;
                using NetworkStream stream = client.GetStream();
                using StreamReader reader = new(stream);
                using StreamWriter writer = new(stream) { AutoFlush = true, NewLine = "\n" };
                Require(reader.ReadLine() == "HELLO uwb-fixture/1", "Incorrect controlled fixture HELLO.");
                writer.WriteLine("GO");
                Require(reader.ReadLine() == "ACK uwb-fixture/1", "Incorrect controlled fixture ACK.");
                Require(Wait(() => capture.Process.HasExited), "The handshake fixture did not exit.");
                Require(capture.Process.ExitCode == 0, "The handshake fixture exit code is not zero.");
                capture.Output();
            }
            finally { listener.Stop(); }
        }

        private static void FailedStart()
        {
            WindowProcess process = new();
            try
            {
                try
                {
                    process.StartProcess(Path.Combine(work, "missing-fixture.exe"), work, "", _ => { }, _ => { });
                    throw new InvalidOperationException("A missing fixture unexpectedly started.");
                }
                catch (System.ComponentModel.Win32Exception) { }
                Require(process.HasExited, "A failed start has a live native process.");
            }
            finally { process.Dispose(); process.Dispose(); }
        }

        private static void CancelBeforeStart()
        {
            using WindowProcess process = new();
            process.KillProcess();
            process.KillProcess();
            try
            {
                process.StartProcess(Environment.ProcessPath, work, "--fixture natural", _ => { }, _ => { });
                throw new InvalidOperationException("A cancelled process subsequently started.");
            }
            catch (OperationCanceledException) { }
        }

        private static void TreeCleanup() => KillTree(false);
        private static void OrphanCleanup() => KillTree(true);

        private static void KillTree(bool parentExits)
        {
            string directory = Path.Combine(work, parentExits ? "orphan" : "tree");
            Directory.CreateDirectory(directory);
            List<System.Diagnostics.Process> owned = new();
            using Capture capture = new();
            try
            {
                capture.Start((parentExits ? "orphan " : "tree ") + Quote(directory));
                Require(Wait(() => new[] { "worker", "leaf" }.All(name => File.Exists(Path.Combine(directory, name + ".pid")))), "Fixture descendants did not initialise.");
                foreach (string name in new[] { "worker", "leaf" })
                    owned.Add(System.Diagnostics.Process.GetProcessById(int.Parse(File.ReadAllText(Path.Combine(directory, name + ".pid")))));
                if (parentExits)
                {
                    Require(Wait(() => capture.Process.HasExited), "The orphan parent did not exit.");
                    capture.Process.Dispose();
                }
                else
                {
                    capture.Process.KillProcess();
                    capture.Process.KillProcess();
                    Require(Wait(() => capture.Process.HasExited), "The parent did not terminate.");
                }
                Require(Wait(() => owned.All(process => process.HasExited)), "The job left a running descendant.");
            }
            finally
            {
                foreach (System.Diagnostics.Process process in owned)
                {
                    if (!process.HasExited)
                    {
                        Console.WriteLine("{\"harnessEmergencyCleanupPid\":" + process.Id + "}");
                        process.Kill();
                        process.WaitForExit(2000);
                    }
                    process.Dispose();
                }
            }
        }

        private static int Fixture(string[] args)
        {
            if (args.Length == 0) return 2;
            Console.Error.WriteLine("FIXTURE_STDERR");
            byte[] utf8 = Encoding.UTF8.GetBytes(Sentinel + "\n");
            Stream stdout = Console.OpenStandardOutput();
            foreach (byte value in utf8)
            {
                stdout.WriteByte(value);
                stdout.Flush();
                Thread.Sleep(1);
            }
            if (args[0] == "natural") return 17;
            if (args[0] == "argument") { Console.WriteLine("ARG=" + args[1]); return 0; }
            if (args[0] == "handshake")
            {
                using TcpClient client = new();
                client.Connect(IPAddress.Loopback, int.Parse(args[1]));
                using NetworkStream stream = client.GetStream();
                using StreamReader reader = new(stream);
                using StreamWriter writer = new(stream) { AutoFlush = true, NewLine = "\n" };
                writer.WriteLine("HELLO uwb-fixture/1");
                Require(reader.ReadLine() == "GO", "The fixture received an invalid GO.");
                writer.WriteLine("ACK uwb-fixture/1");
                return 0;
            }
            string directory = args[1];
            if (args[0] == "tree" || args[0] == "orphan")
            {
                Spawn("worker", directory);
                Require(Wait(() => File.Exists(Path.Combine(directory, "leaf.pid"))), "The fixture leaf did not initialise.");
                if (args[0] == "orphan") return 0;
            }
            else
            {
                File.WriteAllText(Path.Combine(directory, args[0] + ".pid"), Environment.ProcessId.ToString());
                if (args[0] == "worker") Spawn("leaf", directory);
            }
            while (true) Thread.Sleep(100);
        }

        private static void Spawn(string mode, string directory)
        {
            // Independent .NET fixture creation, not the adapter under test or Unity.
            using System.Diagnostics.Process child = System.Diagnostics.Process.Start(new ProcessStartInfo(Environment.ProcessPath)
            {
                Arguments = "--fixture " + mode + " " + Quote(directory),
                UseShellExecute = false,
                CreateNoWindow = true
            });
        }

        private sealed class Capture : IDisposable
        {
            internal readonly WindowProcess Process = new();
            internal readonly ConcurrentQueue<string> Stdout = new();
            private readonly ConcurrentQueue<string> stderr = new();
            private readonly ManualResetEventSlim stdoutEof = new();
            private readonly ManualResetEventSlim stderrEof = new();
            internal void Start(string arguments) => Process.StartProcess(Environment.ProcessPath, work, "--fixture " + arguments,
                line => { if (line == null) stdoutEof.Set(); else Stdout.Enqueue(line); },
                line => { if (line == null) stderrEof.Set(); else stderr.Enqueue(line); });
            internal void Output()
            {
                Require(stdoutEof.Wait(5000) && stderrEof.Wait(5000), "A pipe EOF callback was not delivered.");
                Require(stderr.Contains("FIXTURE_STDERR"), "The stderr sentinel was not delivered.");
            }
            public void Dispose() { Process.Dispose(); }
        }
    }
}

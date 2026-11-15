using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("象棋指导棋")]
[assembly: AssemblyProduct("象棋指导棋")]
[assembly: AssemblyDescription("象棋指导棋 · 双引擎分析工作台")]
[assembly: AssemblyCompany("")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("1.0.0.0")]

namespace XiangqiCoachLauncher
{
    internal static class Program
    {
        private const string Caption = "象棋指导棋";

        [STAThread]
        private static void Main()
        {
            try
            {
                Application.EnableVisualStyles();
                string baseDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
                string programDir = Path.Combine(baseDir, "程序");
                string entry = Path.Combine(programDir, "启动.pyw");

                if (!File.Exists(entry))
                {
                    // The launcher may also sit inside the program folder.
                    string alternate = Path.Combine(baseDir, "启动.pyw");
                    if (File.Exists(alternate))
                    {
                        entry = alternate;
                        programDir = baseDir;
                    }
                    else
                    {
                        ShowError("没有找到程序文件：\n" + entry +
                            "\n\n请确认“程序”文件夹和“象棋指导棋.exe”在同一个目录下，且不要单独移动 exe。");
                        return;
                    }
                }

                string python = FindPython(baseDir);
                if (python == null)
                {
                    ShowError("没有找到可用的 Python 3.11 或更高版本。\n\n" +
                        "本机的开发版需要安装 Python 3.11+；朋友使用的免安装包应带有 runtime\\python.exe。");
                    return;
                }

                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = python;
                info.Arguments = "-X utf8 \"" + entry + "\"";
                info.WorkingDirectory = programDir;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                Process.Start(info);
            }
            catch (Exception exc)
            {
                ShowError("启动失败：\n" + exc.Message +
                    "\n\n请尝试运行“程序\\启动（控制台诊断）.bat”查看完整错误信息。");
            }
        }

        private static string FindPython(string baseDir)
        {
            // Portable package first.
            string portable = Path.Combine(Path.Combine(baseDir, "runtime"), "python.exe");
            if (File.Exists(portable)) return portable;

            string[] versions = new string[] { "314", "313", "312", "311" };

            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (!String.IsNullOrEmpty(local))
            {
                foreach (string version in versions)
                {
                    string candidate = Path.Combine(local, "Programs\\Python\\Python" + version + "\\python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
            }

            string programFiles = Environment.GetEnvironmentVariable("ProgramFiles");
            if (!String.IsNullOrEmpty(programFiles))
            {
                foreach (string version in versions)
                {
                    string candidate = Path.Combine(programFiles, "Python" + version + "\\python.exe");
                    if (File.Exists(candidate)) return candidate;
                }
            }

            string registry = FindPythonInRegistry();
            if (registry != null) return registry;

            string path = Environment.GetEnvironmentVariable("PATH");
            if (!String.IsNullOrEmpty(path))
            {
                string[] parts = path.Split(';');
                foreach (string part in parts)
                {
                    if (String.IsNullOrWhiteSpace(part)) continue;
                    try
                    {
                        string candidate = Path.Combine(part.Trim(), "python.exe");
                        if (File.Exists(candidate)) return candidate;
                    }
                    catch (ArgumentException)
                    {
                    }
                }
            }
            return null;
        }

        private static string FindPythonInRegistry()
        {
            RegistryKey[] roots = new RegistryKey[] { Registry.LocalMachine, Registry.CurrentUser };
            foreach (RegistryKey root in roots)
            {
                try
                {
                    using (RegistryKey core = root.OpenSubKey("Software\\Python\\PythonCore"))
                    {
                        if (core == null) continue;
                        foreach (string name in core.GetSubKeyNames())
                        {
                            using (RegistryKey install = core.OpenSubKey(name + "\\InstallPath"))
                            {
                                if (install == null) continue;
                                object value = install.GetValue(null);
                                if (value == null) continue;
                                string candidate = Path.Combine(value.ToString(), "python.exe");
                                if (File.Exists(candidate)) return candidate;
                            }
                        }
                    }
                }
                catch (Exception)
                {
                }
            }
            return null;
        }

        private static void ShowError(string text)
        {
            try
            {
                MessageBox.Show(text, Caption, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            catch (Exception)
            {
                try
                {
                    File.AppendAllText(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "启动器错误.log"),
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "\r\n" + text + "\r\n\r\n", Encoding.UTF8);
                }
                catch (Exception)
                {
                }
            }
        }
    }
}

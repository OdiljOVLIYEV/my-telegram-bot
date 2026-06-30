import tkinter as tk
from tkinter import messagebox, scrolledtext
import subprocess
import threading
import sys
import os

class DatabaseGUIManager:
    def __init__(self, root):
        self.root = root
        self.root.title("MongoDB Manager - TG Bot")
        self.root.geometry("600x500")
        self.root.configure(bg="#2c3e50")

        # Title
        self.title_label = tk.Label(
            root, text="Baza Boshqaruvi", 
            font=("Helvetica", 18, "bold"), 
            bg="#2c3e50", fg="#ecf0f1"
        )
        self.title_label.pack(pady=20)

        # Buttons Frame
        self.btn_frame = tk.Frame(root, bg="#2c3e50")
        self.btn_frame.pack(pady=10)

        # Download Button
        self.download_btn = tk.Button(
            self.btn_frame, text="📥 Bazani Yuklab Olish (Backup)", 
            font=("Helvetica", 12), bg="#27ae60", fg="white",
            width=30, height=2, command=self.run_download
        )
        self.download_btn.grid(row=0, column=0, pady=10)

        # Restore Button
        self.restore_btn = tk.Button(
            self.btn_frame, text="📤 Bazani Tiklash (Restore)", 
            font=("Helvetica", 12), bg="#e67e22", fg="white",
            width=30, height=2, command=self.confirm_restore
        )
        self.restore_btn.grid(row=1, column=0, pady=10)

        # Output Area
        self.output_area = scrolledtext.ScrolledText(
            root, width=70, height=15, 
            bg="#34495e", fg="#ecf0f1", 
            font=("Consolas", 10)
        )
        self.output_area.pack(pady=20, padx=20)
        self.output_area.insert(tk.END, "Tayyor. Amallardan birini tanlang...\n")
        self.output_area.configure(state='disabled')

    def log(self, message):
        self.output_area.configure(state='normal')
        self.output_area.insert(tk.END, message + "\n")
        self.output_area.see(tk.END)
        self.output_area.configure(state='disabled')

    def run_command(self, command):
        def thread_target():
            self.download_btn.config(state='disabled')
            self.restore_btn.config(state='disabled')
            
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, 
                text=True, shell=True, bufsize=1, universal_newlines=True
            )

            for line in process.stdout:
                self.log(line.strip())
            
            process.wait()
            self.download_btn.config(state='normal')
            self.restore_btn.config(state='normal')
            
            if process.returncode == 0:
                self.log("\n✅ Amall muvaffaqiyatli yakunlandi.")
            else:
                self.log(f"\n❌ Xatolik yuz berdi. Kod: {process.returncode}")

        threading.Thread(target=thread_target, daemon=True).start()

    def run_download(self):
        self.log("\n--- Yuklab olish boshlandi ---")
        self.run_command(f"{sys.executable} download_db.py")

    def confirm_restore(self):
        answer = messagebox.askyesno(
            "Tasdiqlash", 
            "DIQQAT: Bazadagi mavjud ma'lumotlar o'chiriladi va backupdan tiklanadi. Davom etamizmi?"
        )
        if answer:
            self.log("\n--- Tiklash boshlandi ---")
            self.run_command(f"{sys.executable} restore_db.py --force")
        else:
            self.log("\nTiklash bekor qilindi.")

if __name__ == "__main__":
    root = tk.Tk()
    app = DatabaseGUIManager(root)
    root.mainloop()

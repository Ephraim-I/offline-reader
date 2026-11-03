import tkinter as tk
import threading

from services.api_client import ask

class AnalyzerUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Text / Log Analyzer")

        # Input area
        self.input_text = tk.Text(
            self.root, height=15, wrap="word"
        )

        self.input_text.pack(
            fill="both", expand=True, padx=10, pady=5
        )

        # Analyze button
        self.analyze_btn = tk.Button(
            self.root, text="Analyze", command=self.analyze
        )

        self.analyze_btn.pack(padx=5)

        # Output area
        self.output_text = tk.Text(
            self.root, height="10", bg="#111", fg="#ddd"
        )
        self.output_text.pack(fill="both", expand=True, padx=10, pady=5)

    def analyze(self):
        text = self.input_text.get("1.0", tk.END).strip()

        if not text:
            return
            
        self.output_text.delete("1.0", tk.END)

        self.output_text.insert(tk.END, "Analyzing...")

        thread = threading.Thread(
            target=self.run_analysis, args=(text,), daemon=True
        )
        thread.start()

    def run_analysis(self, text):
        prompt = f"""
Analyze this text or log and explain clearly:
{text}

- What is happening?
- Any issues?
- Keep it simple and structured
"""
        
        result = ask(prompt)

        self.root.after(0, lambda: self.display_result(result))

    def display_result(self, result):
        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, result)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = AnalyzerUI()
    app.run()


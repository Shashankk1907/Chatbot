import os

# ====== CONFIG ======
root_folder = r"/Users/shashank/Downloads/chatbot-history-management-main/frontend/src"   # Change this
output_file = "merged_output.txt"

# Add or remove file extensions as needed
code_extensions = {
    ".py", ".js", ".ts",".jsx", ".java", ".c", ".cpp", ".cs",
    ".html", ".css", ".php", ".rb", ".go", ".rs",
    ".swift", ".kt", ".m", ".sql", ".sh", ".ps1",
    ".json", ".xml", ".yaml", ".yml"
}
# =====================

def is_code_file(filename):
    return os.path.splitext(filename)[1].lower() in code_extensions

with open(output_file, "w", encoding="utf-8") as outfile:
    for foldername, subfolders, filenames in os.walk(root_folder):
        for filename in filenames:
            if is_code_file(filename):
                file_path = os.path.join(foldername, filename)
                
                try:
                    with open(file_path, "r", encoding="utf-8") as infile:
                        outfile.write("\n")
                        outfile.write("=" * 80 + "\n")
                        outfile.write(f"FILE: {file_path}\n")
                        outfile.write("=" * 80 + "\n\n")
                        outfile.write(infile.read())
                        outfile.write("\n\n")
                except Exception as e:
                    print(f"Skipped {file_path}: {e}")

print("✅ All code files merged into", output_file)
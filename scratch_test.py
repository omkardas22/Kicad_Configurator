import customtkinter as ctk

app = ctk.CTk()
app.geometry("400x300")

# Horizontal scrollable frame
scroll = ctk.CTkScrollableFrame(app, orientation="horizontal")
scroll.pack(fill="both", expand=True)

# Add 4 columns that are wide
for i in range(4):
    col = ctk.CTkFrame(scroll, width=200, height=200)
    col.grid(row=0, column=i, padx=10, pady=10)
    ctk.CTkLabel(col, text=f"Column {i} a very long text that needs width to show").pack()

app.mainloop()

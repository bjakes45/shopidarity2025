# 🛒 Shopidarity

**Shopidarity** is a collaborative platform designed to help people **save money by organizing around their purchasing habits**. It provides product lookup tools, community features, and the foundation for group-based deal sharing and bulk buying.

---

## 🔧 Setup Instructions

1. **Clone the repository**
   ```bash
   git clone https://github.com/bjakes45/shopidarity2025.git
   cd shopidarity2025
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   - Create a `.env` file and add your secret key:
     ```
     SECRET_KEY=your_secret_key_here
     ```

5. **Run the app**
   ```bash
   flask run
   ```

---

## ✨ Features

- ✅ Product search by UPC and keyword  
- 🔄 UPC lookup with fallback to multiple external APIs  
- 📦 Offers (deals) tracking per product  
- 👥 User authentication  
- 🧠 Plans for user groups, bulk buy coordination, and social features  

---

## 🚧 Roadmap

- [ ] Add user-submitted offers and deal sharing  
- [ ] Implement group features for collaborative buying  
- [ ] Match users by geography and shared interests  
- [ ] Add an admin dashboard and analytics  

---

## 🤝 Contributing

We welcome ideas, feedback, and help! Fork the repo, create a branch, and submit a pull request.

---

## 📄 License

This project is licensed under the MIT License.

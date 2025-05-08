from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    name = db.Column(db.String)
    location = db.Column(db.String)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    upc = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(256), nullable=True)
    category = db.Column(db.String(128), nullable=True)
    brand = db.Column(db.String(128), nullable=True)

    def __repr__(self):
        return f'<Product {self.upc} - {self.title}>'

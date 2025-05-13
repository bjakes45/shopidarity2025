from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from enum import Enum
from sqlalchemy import Enum as SQLEnum


db = SQLAlchemy()



class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    city = db.Column(db.String(100))

    def shared_interest_score(self, other_user):
        """Compute shared interest score with another user based on favorites and ratings."""

        # Get product IDs for this user
        my_fav_ids = set(f.product_upc for f in self.favorites)
        my_rated_ids = set(r.product_upc for r in self.ratings)

        # Get product IDs for the other user
        their_fav_ids = set(f.product_upc for f in other_user.favorites)
        their_rated_ids = set(r.product_upc for r in other_user.ratings)

        # Shared products
        shared_favs = my_fav_ids.intersection(their_fav_ids)
        shared_ratings = my_rated_ids.intersection(their_rated_ids)

        # Weighted score: favorites = 1 point, ratings = 0.5 point
        score = len(shared_favs) + 0.5 * len(shared_ratings)

        return score

class ProductStatus(Enum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"

class Product(db.Model):
    upc = db.Column(db.String(20), unique=True, nullable=False, primary_key=True)
    name = db.Column(db.String(512), nullable=True)
    category = db.Column(db.String(128), nullable=True)
    brand = db.Column(db.String(128), nullable=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(512), nullable=True)
    nutriments = db.Column(db.JSON, nullable=True)

    status = db.Column(SQLEnum(ProductStatus), default=ProductStatus.SUGGESTED, nullable=False)

    # NEW FIELDS
    suggested_by_ip = db.Column(db.String(64), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship("User", backref="products")

    deals = db.relationship('Deal', back_populates='product', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Product {self.upc} - {self.name}>'

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='favorites')

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # 1 to 5
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='ratings')


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_public = db.Column(db.Boolean, default=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='comments')

class Deal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)    
    price = db.Column(db.Float, nullable=False)
    store = db.Column(db.String(120), nullable=False)
    url = db.Column(db.String(300))
    location = db.Column(db.String(120))
    source = db.Column(db.String(50), default="user")
    date_found = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=True)

    product = db.relationship('Product', back_populates='deals')

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    image_path = db.Column(db.String(256), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    parsed_data = db.Column(db.Text)  # Raw OCR text or structured JSON

    # Relationships
    deals = db.relationship('Deal', backref='receipt', lazy=True)



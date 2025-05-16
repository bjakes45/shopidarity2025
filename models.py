from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from enum import Enum
from sqlalchemy import func
from sqlalchemy import Enum as SQLEnum
from geopy.distance import geodesic


db = SQLAlchemy()



class User(db.Model, UserMixin):
    #LOGIN CREDENTIALS
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    admin = db.Column(db.Boolean, default=False)
    
    #GEODATA
    city = db.Column(db.String(100))
    country = db.Column(db.String(100))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

    #SCHEMA RELATIONSHIPS
    favorites = db.relationship('Favorite', back_populates='user', cascade='all, delete-orphan')
    ratings = db.relationship('Rating', back_populates='user', cascade='all, delete-orphan')
    deals = db.relationship('Deal', back_populates='user')
    
    #METHODS
    def shared_favorites(self, other_user):
        """Return a set of shared favorite product UPCs."""
        my_fav_ids = set(f.product_upc for f in self.favorites)
        their_fav_ids = set(f.product_upc for f in other_user.favorites)
        
        return my_fav_ids & their_fav_ids

    def shared_ratings(self, other_user, return_matches=False):
        """Return a similarity score based on rating values. Optionally return matched products."""

        # Build rating dictionaries
        my_ratings_dict = {r.product_upc: r.score for r in self.ratings}
        their_ratings_dict = {r.product_upc: r.score for r in other_user.ratings}

        shared_rated_ids = set(my_ratings_dict.keys()) & set(their_ratings_dict.keys())
        rating_score = 0
        matches = []

        for upc in shared_rated_ids:
            my_score = my_ratings_dict[upc]
            their_score = their_ratings_dict[upc]
            diff = abs(my_score - their_score)
            similarity = 1 - (diff / 4)  # normalized [0, 1]
            rating_score += similarity
            if return_matches:
                matches.append((upc, my_score, their_score, similarity))

        rating_score *= 0.75

        return (rating_score, matches) if return_matches else rating_score

    def shared_interest_score(self, other_user):
        shared_favs = self.shared_favorites(other_user)
        fav_score = len(shared_favs)
        rating_score = self.shared_ratings(other_user)
        return fav_score + rating_score

    def distance_from(self, other_):
        """Returns the geodesic distance in km to another user."""
        if not self.latitude or not self.longitude or not other_.latitude or not other_.longitude:
            return None  # Return None if any coords are missing
        return geodesic(
            (self.latitude, self.longitude),
            (other_.latitude, other_.longitude)
        ).km

    def nearby_users(self, radius_km=30):
        nearby = []

        for user in User.query.filter(User.id != self.id).all():
            dist = self.distance_from(user)
            if dist is not None and dist <= radius_km:
                user.distance_km = round(dist, 1)  # Attach for template rendering
                nearby.append(user)

        nearby.sort(key=lambda u: u.distance_km)
        return nearby

    def nearby_deals(self, radius_km=30):
        nearby = []

        for deal in Deal.query.all():
            dist = self.distance_from(deal)
            if dist is not None and dist <= radius_km:
                deal.distance_km = round(dist, 1)  # Attach for template rendering
                nearby.append(deal)

        nearby.sort(key=lambda u: u.distance_km)
        return nearby


class ProductStatus(Enum):
    SUGGESTED = "suggested"
    APPROVED = "approved"
    REJECTED = "rejected"

class Product(db.Model):
    upc = db.Column(db.String(20), unique=True, nullable=False, primary_key=True)
    name = db.Column(db.String(512), nullable=True)
    category = db.Column(db.Text, nullable=True)
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

    def get_rating(self, user):
        return Rating.query.filter_by(product_upc=self.upc, user_id=user.id).first()

    def average_rating(self): 
      return db.session.query(func.avg(Rating.score)).filter_by(product_upc=self.upc).scalar()

    def average_price(self):
        prices = [deal.price for deal in self.deals if deal.price is not None]
        return round(sum(prices) / len(prices), 2) if prices else None
    
    @property
    def collective_carts(self):
        return [cart for deal in self.deals for cart in deal.collective_carts]

    def __repr__(self):
        return f'<Product {self.upc} - {self.name}>'

class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='favorites')
    user = db.relationship('User', back_populates='favorites')

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # 1 to 5
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='ratings')
    user = db.relationship('User', back_populates='ratings')


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
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    source = db.Column(db.String(50), default="user")
    date_found = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=True)

    user = db.relationship('User', back_populates='deals')
    product = db.relationship('Product', back_populates='deals')
    collective_carts = db.relationship('CollectiveCart', back_populates='deal', cascade="all, delete-orphan")

class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    image_path = db.Column(db.String(256), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    parsed_data = db.Column(db.Text)  # Raw OCR text or structured JSON

    # Relationships
    deals = db.relationship('Deal', backref='receipt', lazy=True)

class CollectiveCart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'), nullable=False)

    host_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    share_count = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_fulfilled = db.Column(db.Boolean, default=False)

    host = db.relationship('User', backref='hosted_collective_carts')
    deal = db.relationship('Deal', back_populates='collective_carts')
    shares = db.relationship('CartShare', back_populates='cart', cascade="all, delete-orphan")

    @property
    def product(self):
        return self.deal.product if self.deal else None

    def share_cost(self):
        return self.total_cost / self.share_count

class CartShare(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey('collective_cart.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    amount_paid = db.Column(db.Float, nullable=False)
    fulfilled = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref='cart_shares')
    cart = db.relationship('CollectiveCart', back_populates='shares')

class APIUsage(db.Model):
    __tablename__ = 'api_usage'

    ip = db.Column(db.String(45), primary_key=True)  # IPv6-safe
    count = db.Column(db.Integer, default=0)
    remaining = db.Column(db.Integer, default=100)
    reset_time = db.Column(db.DateTime, default=lambda: datetime.utcnow() + timedelta(days=1))
    reset_timestamp = db.Column(db.DateTime, nullable=True)

    def reset(self):
        self.count = 1
        self.remaining = 10
        self.reset_time = datetime.utcnow() + timedelta(days=1)


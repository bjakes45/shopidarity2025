from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timedelta, date
from enum import Enum
from sqlalchemy import func
from sqlalchemy import Enum as SQLEnum
from geopy.distance import geodesic
from werkzeug.security import generate_password_hash,check_password_hash
import json



db = SQLAlchemy()



class User(db.Model, UserMixin):
    #LOGIN CREDENTIALS

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    admin = db.Column(db.Boolean, default=False)
    deleted = db.Column(db.Boolean, default=False)

    
    #GEODATA
    city = db.Column(db.String(100))
    country = db.Column(db.String(100))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

    #Settings
    default_fav_alert = db.Column(db.Boolean, default=True)
    deal_distance = db.Column(db.Float, default=20.0)  # distance in km or miles

    #Favorites limited to increase curation incentive
    max_favorites = db.Column(db.Integer, default=10)
    
    #SCHEMA RELATIONSHIPS
    favorites = db.relationship('Favorite', back_populates='user', cascade='all, delete-orphan')
    ratings = db.relationship('Rating', back_populates='user', cascade='all, delete-orphan')
    comments = db.relationship('Comment', back_populates='user', cascade='all, delete-orphan')
    deals = db.relationship('Deal', back_populates='user')
    groups = db.relationship('Group', back_populates='user')
    user_badges = db.relationship('UserBadge', backref='user')
    
    #METHODS
    def check_password(self, password):
        return check_password_hash(self.password, password)

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

    origin = db.Column(db.String(128), nullable=True)
    verified_by = db.Column(db.String(128), nullable=True)
    status = db.Column(SQLEnum(ProductStatus), default=ProductStatus.SUGGESTED, nullable=False)
    deleted = db.Column(db.Boolean, default=False)


    # NEW FIELDS
    suggested_by_ip = db.Column(db.String(64), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    user = db.relationship("User", backref="products")

    deals = db.relationship('Deal', back_populates='product', cascade='all, delete-orphan')

    def favorited_by_to_alert(self, deal):
      if not deal.latitude or not deal.longitude:
        return []

      deal_coords = (deal.latitude, deal.longitude)
      valid_users = []

      for fav in self.favorites:
          user = fav.user
          if not fav.to_alert:
              continue
          if user.latitude is None or user.longitude is None:
              continue

          user_coords = (user.latitude, user.longitude)
          distance = geodesic(deal_coords, user_coords).km
          max_distance = user.deal_distance or 10.0  # default if not set

          if distance <= max_distance:
              valid_users.append(user)

      return valid_users

    def get_rating(self, user):
        return Rating.query.filter_by(product_upc=self.upc, user_id=user.id).first()

    def average_rating(self): 
          return db.session.query(func.avg(Rating.score)).filter_by(product_upc=self.upc).scalar()

    def average_price(self, start_date: datetime = None, end_date: datetime = None):
        filtered_deals = self.deals
        if start_date:
            filtered_deals = [deal for deal in filtered_deals if deal.date_found >= start_date]
        if end_date:
            filtered_deals = [deal for deal in filtered_deals if deal.date_found <= end_date]
        prices = [deal.price for deal in filtered_deals if deal.price is not None]
        return round(sum(prices) / len(prices), 2) if prices else None

    def average_price_over_time(self, interval='week', start_date=None, end_date=None):
        """
        Returns a dict of {interval_start: average_price} for this product's deals.

        Args:
            interval (str): 'day', 'week', or 'month'
            start_date (date): Optional filter start date
            end_date (date): Optional filter end date
        """
        # Filter deals with valid prices and dates
        deals = [
            (deal.date_found, deal.price)
            for deal in self.deals
            if deal.price is not None and deal.date_found is not None
        ]

        # Apply optional date filters
        if start_date:
            deals = [(d, p) for d, p in deals if d >= start_date]
        if end_date:
            deals = [(d, p) for d, p in deals if d <= end_date]

        # Group by time interval
        buckets = defaultdict(list)
        for date_found, price in deals:
            if interval == 'day':
                key = date_found
            elif interval == 'week':
                key = date_found - timedelta(days=date_found.weekday())  # Monday of the week
            elif interval == 'month':
                key = date_found.replace(day=1)
            else:
                raise ValueError("Invalid interval. Use 'day', 'week', or 'month'.")

            buckets[key].append(price)

        # Return average price per time bucket
        return {
            bucket_start: round(sum(prices) / len(prices), 2)
            for bucket_start, prices in sorted(buckets.items())
        }

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
    to_alert = db.Column(db.Boolean, default=True)

    product = db.relationship('Product', backref='favorites')
    user = db.relationship('User', back_populates='favorites')

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # 1 to 5
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'), nullable=True)
    
    product = db.relationship('Product', backref='ratings', lazy='joined')
    deal = db.relationship('Deal', backref='ratings', lazy='joined')
    user = db.relationship('User', back_populates='ratings')



class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_public = db.Column(db.Boolean, default=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    deleted = db.Column(db.Boolean, default=False)


    user = db.relationship('User', back_populates='comments')
    product = db.relationship('Product', backref='comments')



class Receipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    image_path = db.Column(db.String(256), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    parsed_data = db.Column(db.Text)  # Raw OCR text or structured JSON
    deleted = db.Column(db.Boolean, default=False)


    # Relationships
    deals = db.relationship('Deal', backref='receipt', lazy=True)

class Deal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.String, db.ForeignKey('product.upc'), nullable=False)    
    price = db.Column(db.Float, nullable=False)
    store = db.Column(db.String(120))
    url = db.Column(db.String(300))
    description = db.Column(db.Text, nullable=True)
    on_sale = db.Column(db.Boolean, default=False)
    expiry = db.Column(db.DateTime, nullable=True)

    privacy = db.Column(db.String(50), default="user")
    payment_timing = db.Column(db.String(50), default="user")
    payment_method = db.Column(db.String(50), default="user")

    location = db.Column(db.String(120))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    
    source = db.Column(db.String(50), default="user")
    date_found = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('receipt.id'), nullable=True)
    
    deleted = db.Column(db.Boolean, default=False)


    user = db.relationship('User', back_populates='deals')
    product = db.relationship('Product', back_populates='deals')
    collective_carts = db.relationship('CollectiveCart', back_populates='deal', cascade="all, delete-orphan")

    def average_rating(self):
        return db.session.query(func.avg(Rating.score)).filter_by(deal_id=self.id).scalar()

class CollectiveCart(db.Model):
    __tablename__ = 'collective_cart'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey('deal.id'), nullable=False)
    host_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    share_count = db.Column(db.Integer, nullable=False)
    max_shares = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_fulfilled = db.Column(db.Boolean, default=False)
    deleted = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text, nullable=True)

    pickup_time = db.Column(db.Time, nullable=False)
    pickup_date = db.Column(db.Date, nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    store_name = db.Column(db.String(120), nullable=True)  # can use deal.store as fallback
    expiry = db.Column(db.DateTime, nullable=True)  # optional unless deal is on_sale
    tax_applied = db.Column(db.Boolean, default=False)
    tax_rate = db.Column(db.Float, default=0.0)
    privacy = db.Column(db.String(50), default='public')  # 'public', 'user_only', 'group_only'
    payment_timing = db.Column(db.String(50))  # 'upfront', 'delivery'
    payment_method = db.Column(db.String(50))  # 'cash', 'etransfer', 'venmo', etc.

    host = db.relationship('User', backref='hosted_collective_carts')
    deal = db.relationship('Deal', back_populates='collective_carts')
    shares = db.relationship('CartShare', back_populates='cart', cascade="all, delete-orphan")

    @property
    def product(self):
        return self.deal.product if self.deal else None

    def share_cost(self):
        return self.total_cost / self.share_count

    def pickup_location(self):
        if self.latitude is not None and self.longitude is not None:
            return f"Near: {abs(self.latitude):.1f}° {'N' if self.latitude >= 0 else 'S'}, " \
                   f"{abs(self.longitude):.1f}° {'E' if self.longitude >= 0 else 'W'}"
        return "Location not available"

class CartShare(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey('collective_cart.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    approved = db.Column(db.Boolean, default=False)
    is_fulfilled = db.Column(db.Boolean, default=False)
    fulfilled_at = db.Column(db.DateTime, nullable=True)
    deleted = db.Column(db.Boolean, default=False)


    user = db.relationship('User', backref='cart_shares')
    cart = db.relationship('CollectiveCart', back_populates='shares')


class Message(db.Model):
    __tablename__ = 'messages'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # nullable for system messages
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    content = db.Column(db.Text, nullable=False)

    read = db.Column(db.Boolean, default=False)
    deleted = db.Column(db.Boolean, default=False)
    archived = db.Column(db.Boolean, default=False)


    sender = db.relationship('User', foreign_keys=[sender_id], backref='sent_messages', lazy='joined')
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref='received_messages', lazy='joined')

    def __repr__(self):
        return f"<Message from {self.sender_id or 'System'} to {self.receiver_id} at {self.sent_at}>"

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    organizer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  
    product_upc = db.Column(db.String, db.ForeignKey('product.upc'), nullable=True)
    product = db.relationship('Product', backref='groups')

    # Represent turf as a central lat/lon and radius (in km)
    center_lat = db.Column(db.Float, nullable=False)
    center_lon = db.Column(db.Float, nullable=False)
    radius_km = db.Column(db.Float, default=30.0)

    invites = db.relationship('GroupInvite', back_populates='group', cascade="all, delete-orphan")
    user = db.relationship('User', backref='group')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    deleted = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Group {self.name}>"

class GroupInvite(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    user = db.relationship('User', foreign_keys=[user_id])
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    group = db.relationship('Group', back_populates='invites')
    invited_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # inviter
    inviter = db.relationship('User', foreign_keys=[invited_by])

    accepted = db.Column(db.Boolean, default=False)
    deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Invite to {self.group.name} for {self.user.email}>"

class Badge(db.Model):
    __tablename__ = 'badges'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    title = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(200))
    fa_icon = db.Column(db.String(80), nullable=False)
    fa_color = db.Column(db.String(80), nullable=False)
    progression_type = db.Column(db.String(80), nullable=False)
    
    progression_json = db.Column(db.Text, nullable=True)

    @property
    def progression(self):
        return json.loads(self.progression_json or '[]')

    @progression.setter
    def progression(self, value):
        self.progression_json = json.dumps(value)

class UserBadge(db.Model):
    __tablename__ = 'user_badges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    badge_id = db.Column(db.Integer, db.ForeignKey('badges.id'), nullable=False)
    color = db.Column(db.String(80))  
    level = db.Column(db.String(80))  # e.g. "Bronze", "Gold"
    progress = db.Column(db.Integer, default=0)

    badge = db.relationship("Badge", backref="user_badges")

    @property
    def next_level_threshold(self):
        """
        Returns the next threshold from the badge's progression list that is higher than current progress.
        If already at max level, returns current progress (or None).
        """
        thresholds = self.badge.progression
        for step in thresholds:
            threshold = step.get("threshold")
            if threshold is not None and self.progress < threshold:
                return threshold
        return thresholds[-1]["threshold"] if thresholds else None

class APIUsage(db.Model):
    __tablename__ = 'api_usage'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    date = db.Column(db.Date, nullable=False, default=date.today)

    # Usage fields (expand as needed)
    lookup_count = db.Column(db.Integer, default=0)
    lookup_remaining = db.Column(db.Integer, default=5)

    login_count = db.Column(db.Integer, default=0)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='unique_daily_user'),
        db.UniqueConstraint('ip', 'date', name='unique_daily_ip'),
    )

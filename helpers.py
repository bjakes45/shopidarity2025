import csv
import random
import requests
import math
import json
from collections import defaultdict
from functools import wraps
from datetime import datetime, timedelta
from flask import flash, redirect, request, jsonify
from flask_login import current_user
from werkzeug.security import generate_password_hash,check_password_hash
from faker import Faker
from math import ceil
from geopy.distance import geodesic

from models import db, Product, Favorite, Rating, Deal, User, APIUsage, Badge, UserBadge  # adjust paths if needed

faker = Faker()

def get_client_ip():
    return request.remote_addr

def get_usage(ip):
    usage = APIUsage.query.get(ip)
    return usage

def enforce_rate_limit(usage, ip):
    ip=ip
    usage = usage
    now = datetime.utcnow()

    if usage:
        if now > usage.reset_time:
            usage.reset(remaining=5)
        elif usage.remaining <= 0:        	
            flash("You have hit your Product entry limit until tomorrow", "error")
            return jsonify({"error": "Rate limit exceeded"}), 429
        else:
            usage.count += 1
            usage.remaining -= 1
            flash("IP: " + str(usage.ip) + " - " + str(usage.remaining) + " Lookups Remaining")
    else:
        usage = APIUsage(
            ip=ip,
            count=1,
            remaining=5,
            reset_time=now + timedelta(days=1)
        )
        db.session.add(usage)
        flash("First use by IP: " + str(usage.ip))

    db.session.commit()
    return None  # No error

def login_required_with_redirect_back(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please sign in to access this feature.", "error")
            return redirect(request.referrer or "/")
        return func(*args, **kwargs)
    return decorated_view

def get_working_image_url(image_urls):
    for url in image_urls:
        try:
            response = requests.head(url, timeout=5)
            if response.status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None  # Return None if no valid URL found

def get_user_favorites(user_id):
    return Product.query.join(Favorite).filter(Favorite.user_id == user_id).all()

def get_user_ratings(user_id):
    return Rating.query.filter_by(user_id=user_id).all()

def get_paginated(to_paginate, page, per_page):
    total = len(to_paginate)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = to_paginate[start:end]
    total_pages = ceil(total / per_page)

    return paginated, page, total_pages, total

def find_similar_users(user_id):
    current_user = User.query.get(user_id)
    all_users = User.query.filter(User.id != user_id).all()

    similar_users = []
    for user in all_users:
    	if user.latitude and user.longitude:
            favs = current_user.shared_favorites(user)
            rating_score = current_user.shared_ratings(user)
            total_score = len(favs) + rating_score
            dist = current_user.distance_from(user)

            if total_score >= 0:
            	user.similarity_score = total_score
            	user.dist = dist
            	user.shared_favorites_list = Product.query.filter(Product.upc.in_(favs)).all()
            	similar_users.append(user)

    # Sort by score and paginate
    similar_users.sort(key=lambda u: u.similarity_score, reverse=True)
    
    return similar_users

def recommend_products(self, other_users):
    # Placeholder: Recommend products in same categories as favorites/ratings
    seen_upcs = {
        f.product_upc for f in self.favorites
    } | {
        r.product_upc for r in self.ratings
    }

    # Count product recommendations from similar users
    recommendation_scores = {}
    
    for other_user in other_users:
        for fav in other_user.favorites:
            if fav.product_upc not in seen_upcs:
                recommendation_scores[fav.product_upc] = recommendation_scores.get(fav.product_upc, 0) + 1

        for rating in other_user.ratings:
            if rating.product_upc not in seen_upcs and rating.score >= 3:
                # Optional: weight rating score
                recommendation_scores[rating.product_upc] = recommendation_scores.get(rating.product_upc, 0) + rating.score / 4

    # Sort by score descending
    sorted_upcs = sorted(recommendation_scores.items(), key=lambda x: x[1], reverse=True)

    # Return full product objects
    upcs = [upc for upc, _ in sorted_upcs]
    
    # Fetch matching products
    products = Product.query.filter(Product.upc.in_(upcs)).all()

    # Reorder products by recommendation score
    product_by_upc = {product.upc: product for product in products}
    ordered_products = [product_by_upc[upc] for upc in upcs if upc in product_by_upc]

    return ordered_products

def get_potential_groups(user):
    if not (user.latitude and user.longitude):
        return []

    user_location = (user.latitude, user.longitude)

    product_upcs = {f.product_upc for f in user.favorites} | {
        r.product_upc for r in user.ratings if r.score >= 3
    }

    group_candidates = defaultdict(lambda: {'product': None, 'interested_users': set()})

    for product_upc in product_upcs:
        product = Product.query.get(product_upc)
        if not product:
            continue

        # Favorited users
        favorited_users = User.query.join(Favorite)\
            .filter(Favorite.product_upc == product_upc, User.id != user.id).all()

        # Rated users
        rated_users = User.query.join(Rating)\
            .filter(Rating.product_upc == product_upc, Rating.score >= 3, User.id != user.id).all()

        all_users = set(favorited_users + rated_users)

        nearby_users = set()
        for other_user in all_users:
            if other_user.latitude and other_user.longitude:
                other_loc = (other_user.latitude, other_user.longitude)
                distance_km = geodesic(user_location, other_loc).km
                if distance_km <= 30:
                    other_user.distance_km = distance_km  # optional: display in UI
                    nearby_users.add(other_user)

        if nearby_users:
            group_candidates[product_upc]['product'] = product
            group_candidates[product_upc]['interested_users'].update(nearby_users)

    potential_groups = sorted(
        group_candidates.values(),
        key=lambda g: len(g['interested_users']),
        reverse=True
    )

    return potential_groups

def evaluate_badge_progress(user, badge_name, increment=1):
    badge = Badge.query.filter_by(name=badge_name).first()
    if not badge:
        return

    user_badge = UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first()

    if not user_badge:
        user_badge = UserBadge(user_id=user.id, badge_id=badge.id, progress=0)
        db.session.add(user_badge)

    user_badge.progress += increment

    # Determine the highest progression level the user qualifies for
    best_level = None
    best_color = None

    for level in sorted(badge.progression, key=lambda x: x['threshold']):
        if user_badge.progress >= level['threshold']:
            best_level = level['level']
            best_color = level['color']
        else:
            break

    # Update if new level/color earned
    if best_level != user_badge.level or best_color != user_badge.color:
        user_badge.level = best_level
        user_badge.color = best_color
        # Optionally: send notification or track achievement here

    db.session.commit()


def random_point_near_vancouver(radius_km=40):
    # Vancouver's coordinates
    center_lat = 49.2827
    center_lng = -123.1207

    # Convert radius from km to degrees
    radius_deg = radius_km / 111  # approx 111 km per degree of latitude

    # Generate a random point within the circle
    u = random.random()
    v = random.random()
    w = radius_deg * math.sqrt(u)
    t = 2 * math.pi * v
    x = w * math.cos(t)
    y = w * math.sin(t)

    # Adjust the x-coordinate for the shrinking of the east-west distances
    new_lat = center_lat + y
    new_lng = center_lng + x / math.cos(math.radians(center_lat))

    return new_lat, new_lng

def evaluate_badge_progress(user, badge_name, increment=1, explicit_progress=None):
    badge = Badge.query.filter_by(name=badge_name).first()
    if not badge:
        return None

    user_badge = UserBadge.query.filter_by(user_id=user.id, badge_id=badge.id).first()
    if not user_badge:
        user_badge = UserBadge(user_id=user.id, badge_id=badge.id, progress=0)
        db.session.add(user_badge)

    # Handle progression types
    if badge.progression_type == 'cumulative':
        user_badge.progress += increment

    elif badge.progression_type == 'max_active':
        # e.g., Number of favorite products or groups joined
        if explicit_progress is not None:
            user_badge.progress = max(user_badge.progress, explicit_progress)

    elif badge.progression_type == 'streak':
        if explicit_progress is not None:
            if explicit_progress > user_badge.progress:
                user_badge.progress = explicit_progress

    # Calculate badge level
    best_level = None
    best_color = None
    for level in sorted(badge.progression, key=lambda x: x['threshold']):
        if user_badge.progress >= level['threshold']:
            best_level = level['level']
            best_color = level['color']
        else:
            break

    level_up = False
    if best_level != user_badge.level or best_color != user_badge.color:
        user_badge.level = best_level
        user_badge.color = best_color
        level_up = True

    db.session.commit()

    if level_up:
        return {
            "badge_name": badge.name,
            "title": badge.title,
            "icon": badge.fa_icon,
            "level": best_level,
            "color": best_color
        }

    return None


#create Items in Database from upc_corpus.csv on server init
def seed_products(i=500):
    with open('static/upc_corpus.csv', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            upc = row['upc'].strip()
            name = row['name'].strip()


            # Avoid duplicates
            if len(upc) == 12:
               if Product.query.filter_by(upc=upc).first() is None:
                   product = Product(upc=upc, name=name, status="APPROVED")
                   db.session.add(product)
                   count += 1

            if count >= i:
                break

        db.session.commit()
        print(f"{count} products seeded.")

def seed_plus(i=2000):
    with open('static/price_lookup_codes.csv', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            upc = row['plu'].strip()
            name = row['variety'].strip() +" "+ row['commodity'].strip()
            category = row['category'].strip()
            image_url = row['image_url'].strip()
            
            # Debug: Output data for every row processed

            # Avoid duplicates
            if Product.query.filter_by(upc=upc).first() is None:
                product = Product(upc=upc, name=name, category=category, image_url=image_url, status="APPROVED", origin='root')
                db.session.add(product)
                count += 1

            if count >= i:
                break

        db.session.commit()
        print(f"{count} products seeded.")

# Create 100 test users and assign 100 favorites and ratings each
def seed_users_with_interactions(user_count=100, interactions_per_user=50):
    products = Product.query.all()
    product_ids = [p.upc for p in products]
      
    count = 0
    for i in range(user_count):
        lat, lng = random_point_near_vancouver()    
        user = User(
        	username=faker.unique.user_name(), 
        	email=faker.unique.email(), 
        	password=generate_password_hash('abc123'), 
        	city='Vancouver',
            country='Canada',
            latitude= lat,
            longitude=lng
        	)
        db.session.add(user)
        db.session.flush()  # get user.id

        fav_product_ids = random.sample(product_ids, interactions_per_user)
        rate_product_ids = random.sample(product_ids, interactions_per_user)

        # Create Favorites
        for pid in fav_product_ids:
            db.session.add(Favorite(user_id=user.id, product_upc=pid))

        # Create Ratings (scale of 1–5)
        for pid in rate_product_ids:
            db.session.add(Rating(user_id=user.id, product_upc=pid, score=random.randint(1, 5)))
        count += 1
    db.session.commit()
    print(f"{count} users seeded.")

def seed_deals(deal_count=100):
    products = Product.query.all()
    product_ids = [p.upc for p in products]

    store_names = ['Safeway', 'No Frills', 'Superstore', 'Walmart', 'Save-On-Foods']
    
    count = 0  # To track how many deals are seeded

    for i in range(deal_count):
        lat, lng = random_point_near_vancouver()
        product_id = random.choice(product_ids)
        store = random.choice(store_names)
        price = round(random.uniform(1.99, 19.99), 2)

        deal = Deal(
            product_id=product_id,
            store=store,
            price=price,
            url=None,
            latitude=lat,
            longitude=lng,
            user_id=None,  # or a valid test user ID
            source="root",
        )
        db.session.add(deal)
        count += 1

    db.session.commit()
    print(f"{count} deals seeded.")

def seed_badges(filepath='static/badges.csv'):
    try:
        with open(filepath, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            db.session.query(Badge).delete()  # Optional: clear existing badges

            for row in reader:
                raw_json = row['progression_json'].strip().replace('""', '"')
                try:
                    json.loads(raw_json)  # Validate it's correct JSON
                except json.JSONDecodeError as e:
                    print(f"Invalid JSON in row '{row['name']}': {e}")
                    continue

                badge = Badge(
                    name=row['name'].strip(),
                    title=row['title'].strip(),
                    description=row['description'].strip(),
                    progression_type=row['progression_type'].strip(),
                    fa_icon=row['fa_icon'].strip(),
                    fa_color=row['fa_color'].strip(),
                    progression_json=raw_json
                )
                db.session.add(badge)

            db.session.commit()
            print("Badges imported successfully.")
    except Exception as e:
        db.session.rollback()
        print(f"Error importing badges: {e}")


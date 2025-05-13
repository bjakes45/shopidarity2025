from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User, Product, Rating, Favorite, Comment, ProductStatus, Deal
from functools import wraps
from datetime import datetime, timedelta
import pandas as pd
import csv, requests
from datetime import datetime
from sqlalchemy import func, or_
import geopy
from geopy.geocoders import Nominatim
import json
from dotenv import load_dotenv
import os


load_dotenv()

#APP CONTEXT
API_USAGE = {
    "count": 0,
    "reset_time": datetime.utcnow() + timedelta(days=1),
    "remaining": 100,  # starting assumption
    "reset_timestamp": None  # from UPCDB header
}

submissions_by_ip = {}

def is_rate_limited(ip):
    timestamps = ip_timestamps(ip)

    if len(timestamps) >= 5:
        return True
    # Not updating timestamps yet — only after success
    submissions_by_ip[ip] = timestamps
    return False

def increment_ip_count(ip):
    now = datetime.utcnow()
    submissions_by_ip.setdefault(ip, []).append(now)

def ip_timestamps(ip):
	now = datetime.utcnow()
	window_start = now - timedelta(days=1)
	timestamps = submissions_by_ip.get(ip, [])
	timestamps = [ts for ts in timestamps if ts > window_start]

	return timestamps

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

#create Items in Database from upc_corpus.csv on server init
def seed_products():
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

            if count >= 5000:
                break

        db.session.commit()
        print(f"{count} products seeded.")

def seed_plus():
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
                product = Product(upc=upc, name=name, category=category, image_url=image_url, status="APPROVED")
                db.session.add(product)
                count += 1

            if count >= 2000:
                break

        db.session.commit()
        print(f"{count} products seeded.")

#Init server and database connection
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
if os.environ.get('RENDER'):
    # Running on Render – require DATABASE_URL
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError("DATABASE_URL not set in environment on Render.")
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Local development fallback
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///app.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


# Initialize Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # name of your login route


@app.before_request
def initialize_database():
    db.create_all()

    if Product.query.count() == 0:
        seed_products()    

    # Ensure PLU seed only happens once
    plu_count = Product.query.filter(db.func.length(Product.upc) == 4).count()
    if plu_count == 0:
        seed_plus()
    else:
        print(plu_count)


# User loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

#HOMEPAGE
@app.route('/')
def home():
	return render_template('home.html', current_year=datetime.now().year)

#UPC SCANNER
@app.route('/scan')
def scan():
	return render_template('scan.html')

#PRODUCTS
@app.route('/products', methods=['GET'])
def products():
    #products_all = Product.query.all()
    # Query all products where UPC is exactly 4 characters long
    products_all = Product.query.filter(db.func.length(Product.upc) == 4).all()


    query = request.args.get('query', '')  # Retrieve UPC query if available

    page = request.args.get('page', 1, type=int)
    per_page = 12

    products = Product.query.filter_by(status=ProductStatus.APPROVED)
    
    if query:
        products = Product.query.filter(
        or_(
            Product.upc.ilike(f"%{query}%"),
            Product.name.ilike(f"%{query}%"),
            Product.description.ilike(f"%{query}%"),
            Product.category.ilike(f"%{query}%")
        )
).filter_by(status=ProductStatus.APPROVED)
    
    pagination = products.paginate(page=page, per_page=per_page)
    
    return render_template('products/products.html', products=products.all(), display=pagination.items, pagination=pagination, query=query, products_all=products_all)

#PRODUCT DETAIL
@app.route('/products/<string:upc>')
def product_detail(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()
    average_rating = db.session.query(func.avg(Rating.score)).filter_by(product_upc=upc).scalar()
    comments = Comment.query.filter_by(product_upc=upc, is_public=True).order_by(Comment.timestamp.desc()).limit(3).all()
    average_price = None	
    total_price = 0
    
    for deal in product.deals:
        total_price += deal.price
        if len(product.deals) > 0:
        	average_price = total_price / len(product.deals)  

	# If logged in, pull user-specific data
    favorite = rating = private_comments = None
    if current_user.is_authenticated:
	    favorite = Favorite.query.filter_by(user_id=current_user.id, product_upc=upc).first()
	    rating = Rating.query.filter_by(user_id=current_user.id, product_upc=upc).first()
	    private_comments = Comment.query.filter_by(user_id=current_user.id, product_upc=upc, is_public=False).all()
    

    return render_template('products/product_detail.html', product=product, average_rating=average_rating, comments=comments, favorite=favorite, rating=rating, private_comments=private_comments, average_price=average_price)

#PRODUCT Nutrifacts
@app.route('/products/<string:upc>/nutrifacts')
def product_nutrifacts(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()

    if product.nutriments == None: 
    	redirect(url_for('product_detail', upc=product.upc))
    

    return render_template('products/product_nutrifacts.html', product=product)

#PRODUCT Deals
@app.route('/products/<string:upc>/deals')
def product_deals(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()

    if product.deals == None: 
    	redirect(url_for('product_detail', upc=product.upc))
    

    return render_template('products/product_deals.html', product=product)

#PRODUCT COMMENTS
@app.route('/products/<string:upc>/comments')
def product_comments(upc):
    product = Product.query.filter_by(upc=upc).first_or_404()
    comments = Comment.query.filter_by(product_upc=upc, is_public=True).order_by(Comment.timestamp.desc()).limit(3).all()

	# If logged in, pull user-specific data
    private_comments = None
    if current_user.is_authenticated:
	    private_comments = Comment.query.filter_by(user_id=current_user.id, product_upc=upc, is_public=False).all()

    return render_template('products/product_comments.html', product=product, comments=comments, private_comments=private_comments)

#NEW PRODUCT
@app.route('/products/new', methods=['GET', 'POST'])
def new_product():
    ip_address = request.remote_addr
    if not is_rate_limited(ip_address):
        increment_ip_count(ip_address)
        flash(f"IP Limit: {len(ip_timestamps(ip_address))} / 5 per day")
    
    is_logged_in = current_user.is_authenticated

    upc = request.args.get('upc', '')  # Retrieve UPC query
    if not upc or len(upc) not in (12, 13):
        flash("Invalid UPC: must be 12 or 13 digits.", "error")
        return redirect(url_for('products'))
    
    existing_product = Product.query.filter_by(upc=upc).first()
    if existing_product:
    	return redirect(url_for('product_detail', upc=existing_product.upc))
    
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        brand = request.form['brand']
        image_url = request.form['image_url']
        nutriments_raw = request.form.get('nutriments')
        try:
            nutriments = json.loads(nutriments_raw) if nutriments_raw else None
        except json.JSONDecodeError:
            nutriments = None  # fallback if invalid
        offers_raw = request.form.get('offers')
        try:
            offers = json.loads(offers_raw) if offers_raw else None
        except json.JSONDecodeError:
            offers = None  # fallback if invalid
        status = ProductStatus.APPROVED if is_logged_in else ProductStatus.SUGGESTED
        user_id = current_user.id if is_logged_in else None

        new_product = Product(
            upc=upc,
            name=name,
            category=category,
            brand=brand,
            image_url=image_url,
            nutriments=nutriments,
            status=status,
            suggested_by_ip=ip_address,
            user_id=user_id
        )
        db.session.add(new_product)
        db.session.flush()

        for offer in offers:
            try:
                deal = Deal(
                    product_id=new_product.upc,
                    price=offer.get("price"),
                    store=offer.get("merchant"),
                    url=offer.get("link"),
                    location=offer.get("region", "N/A"),
                    source="UPCitemDB"
                )
                db.session.add(deal)
            except Exception as e:
                print(f"Bad offer: {e}")
    
        db.session.commit()
    
        return redirect(url_for('product_detail', upc=upc))

    return render_template('products/new_product.html', upc=upc)

#USER DASHBOARD
@app.route('/dashboard/')
@login_required
def dashboard():
    user_id = current_user.id
    favorites = Favorite.query.filter_by(user_id=user_id).order_by(Favorite.timestamp.desc()).limit(3).all()
    ratings = Rating.query.filter_by(user_id=user_id).order_by(Rating.timestamp.desc()).limit(3).all()
    comments = Comment.query.filter_by(user_id=user_id).order_by(Comment.timestamp.desc()).limit(3).all()
    
    return render_template('dashboard/dashboard.html', favorites=favorites, ratings=ratings, comments=comments)

@app.route('/dashboard/added_products')
@login_required
def dashboard_added_products():
    added_products = Product.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/added_products.html', added_products=added_products)

@app.route('/dashboard/favorites')
@login_required
def dashboard_favorites():
    favorites = Favorite.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/favorites.html', favorites=favorites)

@app.route('/dashboard/ratings')
@login_required
def dashboard_ratings():
    ratings = Rating.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/ratings.html', ratings=ratings)

@app.route('/dashboard/comments')
@login_required
def dashboard_comments():
    comments = Comment.query.filter_by(user_id=current_user.id).all()
    
    return render_template('dashboard/comments.html', comments=comments)

#SUGGESTIONS
@app.route("/dashboard/suggestions")
@login_required
def dashboard_suggestions():
    suggestions = Product.query.filter_by(status=ProductStatus.SUGGESTED).all()
    return render_template("dashboard/suggestions.html", products=suggestions, api_calls=API_USAGE["reset_time"])

@app.route("/suggestion/approve/<product_upc>", methods=["POST"])
@login_required
def approve_suggestion(product_upc):
    user_id = current_user.id
    product = Product.query.get_or_404(product_upc)
    product.status = ProductStatus.APPROVED
    product.user_id = user_id
    db.session.commit()
    flash(f"Product '{product.name}' approved.", "success")
    return redirect(url_for("dashboard_suggestions"))


@app.route("/member/reject/<product_upc>", methods=["POST"])
@login_required
def reject_suggestion(product_upc):
    product = Product.query.get_or_404(product_upc)
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{product.name}' rejected and deleted.", "info")
    return redirect(url_for("dashboard_suggestions"))

#CHECK UPC
@app.route('/api/check_upc/<upc>')
def check_upc(upc):
    product = Product.query.filter_by(upc=upc).first()

    if product:
        return jsonify({
            'exists': True,
            'product': {
                'name': product.name,
                'upc': product.upc,
                'category': product.category,
                'brand': product.brand
            }
        })
    else:
        return jsonify({'exists': False})

#API LOOKUP
@app.route('/api/lookup-upc/<upc>')
def lookup_upc(upc):
    # Try Open Food Facts first
    off_url = f"https://world.openfoodfacts.org/api/v0/product/{upc}.json"
    off_response = requests.get(off_url)

    if off_response.status_code == 200:
        off_data = off_response.json()
        if off_data.get('status') == 1:
            product = off_data['product']
            return jsonify({
                "source": "OFF",
                "data": {
                    "name": product.get('product_name', ''),
                    "brand": product.get('brands', ''),
                    "category": product.get('categories', ''),
                    "description": product.get('generic_name', ''),
                    "image_url": product.get("image_url"),
                    "nutriments": product.get("nutriments", {}),
                    "offers": {}
                }
            })

    # If OFF fails, fallback to UPCitemDB
    now = datetime.utcnow()
    if now > API_USAGE["reset_time"]:
        API_USAGE["count"] = 0
        API_USAGE["reset_time"] = now + timedelta(days=1)

    if API_USAGE["count"] >= 100:
        return jsonify({"error": "UPCitemDB daily limit reached."}), 429

    upcdb_response = requests.get(
        f"https://api.upcitemdb.com/prod/trial/lookup?upc={upc}",
        headers={"Content-Type": "application/json"}
    )

    if upcdb_response.ok:
    
        # Extract rate limit headers
        API_USAGE["count"] += 1
        API_USAGE["remaining"] = int(upcdb_response.headers.get("X-RateLimit-Remaining", 100))
        reset_unix = upcdb_response.headers.get("X-RateLimit-Reset")
        if reset_unix:
            API_USAGE["reset_timestamp"] = datetime.utcfromtimestamp(int(reset_unix))
            API_USAGE["reset_time"] = API_USAGE["reset_timestamp"]  # Keep consistent

        data = upcdb_response.json()
        items = data.get("items", [])
        if items:
            item = items[0]
            return jsonify({
                "source": "UPCDB",
                "data": {
                    "name": item.get('title', ''),
                    "brand": item.get('brand', ''),
                    "category": item.get('category', ''),
                    "description": item.get('description', ''),
                    "image_url": get_working_image_url(item.get("images", [])),
                    "nutriments": {},
                    "offers": item.get('offers', '')
                }
            })

    return jsonify({"error": "Product not found"}), 404


#USER HANDLING
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            login_user(user)
            return redirect(url_for('products'))
        else:
            flash('Invalid username or password')
    return render_template('user_admin/login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        hashed_pw = generate_password_hash(request.form['password'])
        new_user = User(username=request.form['username'], email=request.form['email'], password=hashed_pw, city=request.form['city'])
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('user_admin/register.html')

# Whitelist of supported cities
SUPPORTED_CITIES = {'New York', 'Vancouver'}

@app.route('/check-location')
def check_location():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    try:
        geolocator = Nominatim(user_agent="shopidarity")
        location = geolocator.reverse(f"{lat}, {lon}", language='en')
        address = location.raw.get('address', {})
        city = (address.get('city') or address.get('town') or address.get('village') or '').strip().title()

        allowed = city in SUPPORTED_CITIES

        return jsonify({
            "allowed": allowed,
            "city": city,
            "user_count": User.query.filter_by(city=city).count()      # placeholder until we track cities on users
        })
    except Exception as e:
        return jsonify({
            "allowed": False,
            "city": "",
            "user_count": User.query.filter_by(city=city).count(),
            "error": str(e)
        })

@app.route('/product/<upc>/comment', methods=['POST'])
@login_required
def add_comment(upc):
    content = request.form['content']
    is_public = 'is_public' in request.form
    db.session.add(Comment(user_id=current_user.id, product_upc=upc, content=content, is_public=is_public))
    db.session.commit()
    return redirect(url_for('product_detail', upc=upc))

@app.route('/product/<upc>/rate', methods=['POST'])
@login_required
def rate(upc):
    score = int(request.form['score'])
    rating = Rating.query.filter_by(user_id=current_user.id, product_upc=upc).first()
    if rating:
        rating.score = score
    else:
        db.session.add(Rating(user_id=current_user.id, product_upc=upc, score=score))
    db.session.commit()
    return redirect(url_for('product_detail', upc=upc))

@app.route('/product/<upc>/favorite', methods=['POST'])
@login_required
def favorite(upc):
    db.session.add(Favorite(user_id=current_user.id, product_upc=upc))
    db.session.commit()
    return redirect(url_for('product_detail', upc=upc))

@app.route('/product/<upc>/unfavorite', methods=['POST'])
@login_required
def unfavorite(upc):
    Favorite.query.filter_by(user_id=current_user.id, product_upc=upc).delete()
    db.session.commit()
    return redirect(url_for('product_detail', upc=upc))


if __name__ == '__main__':
    app.run(debug=True)
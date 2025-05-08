from flask import Flask, render_template, request, jsonify
from models import db, User, Product
import pandas as pd
import csv

def seed_products():
    with open('static/upc_corpus.csv', newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        count = 0
        for row in reader:
            upc = row['upc'].strip()
            name = row['name'].strip()

            # Avoid duplicates
            if Product.query.filter_by(upc=upc).first() is None:
                product = Product(upc=upc, name=name)
                db.session.add(product)
                count += 1

            if count >= 50000:
                break

        db.session.commit()
        print(f"{count} products seeded.")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

@app.route('/')
def home():
	return render_template('home.html')

@app.route('/scan')
def scan():
	return render_template('scan.html')

@app.route('/products', methods=['GET'])
def products():
    query = request.args.get('query', '')  # Retrieve UPC query if available

    if query:
        # Search for products by UPC or title (partial match)
        products = Product.query.filter(Product.upc.like(f'%{query}%')).all()
    else:
        # If no query, show the first 5 products
        products = Product.query.all()
    
    five_products = products[0:5]

    return render_template('products.html', products=products, query=query, five_products=five_products)


@app.route('/api/check_upc/<upc>')
def check_upc(upc):
    product = Product.query.filter_by(upc=upc).first()
    return jsonify({'exists': product is not None})

@app.route('/add-product')
def add_product():
    upc = request.args.get('upc')
    return f"<h1>Add Product</h1><p>UPC: {upc}</p><p>Coming soon...</p>"

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        if Product.query.count() == 0:
	        seed_products()
    app.run(debug=True)
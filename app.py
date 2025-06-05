from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date
from functools import wraps
from io import BytesIO # For handling PDF in memory
from weasyprint import HTML # For PDF generation

# --- App Configuration ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key__change_this' # CHANGE THIS!
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///hotel_booking.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Login Manager Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Database Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user') # 'user', 'owner', 'admin'
    
    # This relationship uses Hotel.owner_id
    # The backref 'owner_user' will create hotel.owner_user to access the User object from a Hotel instance
    hotels_owned = db.relationship('Hotel', backref='owner_user', lazy=True, foreign_keys='Hotel.owner_id') 
    bookings = db.relationship('Booking', backref='booker', lazy=True, foreign_keys='Booking.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Hotel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price_per_night = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(200), nullable=True, default='https://via.placeholder.com/300x200.png?text=Hotel+Image')
    owner_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Foreign key to User table
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    
    bookings = db.relationship('Booking', backref='hotel', lazy=True, foreign_keys='Booking.hotel_id')

class Booking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    hotel_id = db.Column(db.Integer, db.ForeignKey('hotel.id'), nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    check_out_date = db.Column(db.Date, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='Confirmed')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

# --- Helper Functions ---
def role_required(role_name):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if current_user.role != role_name:
                flash(f"You do not have permission to access this page. Requires '{role_name}' role.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Routes ---
@app.route('/')
def index():
    approved_hotels = Hotel.query.filter_by(is_approved=True).all()
    return render_template('index.html', hotels=approved_hotels)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role', 'user')

        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'danger')
            return redirect(url_for('register'))

        new_user = User(username=username, email=email, role=role)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully!', 'success')
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            elif user.role == 'owner':
                return redirect(url_for('owner_dashboard'))
            else:
                next_page = request.args.get('next')
                return redirect(next_page or url_for('index'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route('/hotel/<int:hotel_id>', methods=['GET', 'POST'])
def hotel_detail(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    if not hotel.is_approved and (not current_user.is_authenticated or current_user.role != 'admin'):
        flash("This hotel is not yet approved or does not exist.", "warning")
        return redirect(url_for('index'))

    if request.method == 'POST':
        if not current_user.is_authenticated:
            flash("Please log in to book a hotel.", "warning")
            return redirect(url_for('login', next=request.url))

        check_in_str = request.form.get('check_in_date')
        check_out_str = request.form.get('check_out_date')

        if not check_in_str or not check_out_str:
            flash("Please select check-in and check-out dates.", "danger")
            return redirect(url_for('hotel_detail', hotel_id=hotel_id))

        try:
            check_in_date = datetime.strptime(check_in_str, '%Y-%m-%d').date()
            check_out_date = datetime.strptime(check_out_str, '%Y-%m-%d').date()
        except ValueError:
            flash("Invalid date format. Please use YYYY-MM-DD.", "danger")
            return redirect(url_for('hotel_detail', hotel_id=hotel_id))

        if check_in_date < date.today():
            flash("Check-in date cannot be in the past.", "danger")
            return redirect(url_for('hotel_detail', hotel_id=hotel_id))
        if check_out_date <= check_in_date:
            flash("Check-out date must be after check-in date.", "danger")
            return redirect(url_for('hotel_detail', hotel_id=hotel_id))

        num_nights = (check_out_date - check_in_date).days
        total_price = num_nights * hotel.price_per_night

        booking = Booking(
            user_id=current_user.id,
            hotel_id=hotel.id,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            total_price=total_price
        )
        db.session.add(booking)
        db.session.commit() 

        flash(f'Successfully booked {hotel.name}! Your booking is confirmed.', 'success')
        return redirect(url_for('booking_confirmed', booking_id=booking.id))

    return render_template('hotel_detail.html', hotel=hotel, today=date.today().isoformat())

@app.route('/booking_confirmed/<int:booking_id>')
@login_required
def booking_confirmed(booking_id):
    booking = Booking.query.get_or_404(booking_id)
    if booking.user_id != current_user.id and current_user.role != 'admin':
        flash("You are not authorized to view this booking confirmation.", "danger")
        return redirect(url_for('index'))
    
    return render_template('booking_confirmed.html', booking=booking)

@app.route('/download_receipt/<int:booking_id>')
@login_required
def download_receipt(booking_id):
    booking = Booking.query.get_or_404(booking_id)

    if booking.user_id != current_user.id and current_user.role != 'admin':
        flash("You are not authorized to download this receipt.", "danger")
        return redirect(url_for('my_bookings'))

    hotel = booking.hotel # Access hotel through booking object
    user = booking.booker # Access user through booking object
    num_nights = (booking.check_out_date - booking.check_in_date).days
    booking_made_date = booking.created_at

    try:
        html_string = render_template('receipt_template.html',
                                      booking=booking,
                                      hotel=hotel,
                                      user=user,
                                      num_nights=num_nights,
                                      booking_date=booking_made_date)

        pdf_bytes = HTML(string=html_string).write_pdf()

        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=receipt_booking_{booking.id}.pdf'
        return response
    except Exception as e:
        app.logger.error(f"Error generating PDF for booking_id {booking.id}: {e}")
        import traceback
        app.logger.error(traceback.format_exc())
        flash("Sorry, there was an error generating your PDF receipt. Please try again or contact support.", "danger")
        if request.referrer and url_for('booking_confirmed', booking_id=booking_id) in request.referrer:
            return redirect(url_for('booking_confirmed', booking_id=booking_id))
        return redirect(url_for('my_bookings'))

@app.route('/my_bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id).order_by(Booking.check_in_date.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)

# --- Hotel Owner Routes ---
@app.route('/register_hotel', methods=['GET', 'POST'])
@role_required('owner')
def register_hotel():
    if request.method == 'POST':
        name = request.form.get('name')
        location = request.form.get('location')
        description = request.form.get('description')
        price_per_night = request.form.get('price_per_night')
        image_url = request.form.get('image_url')

        if not name or not location or not price_per_night:
            flash("Name, location, and price are required.", "danger")
            return redirect(url_for('register_hotel'))
        
        try:
            price_float = float(price_per_night)
        except ValueError:
            flash("Invalid price format.", "danger")
            return redirect(url_for('register_hotel'))

        new_hotel = Hotel(
            name=name,
            location=location,
            description=description,
            price_per_night=price_float,
            image_url=image_url if image_url else 'https://via.placeholder.com/300x200.png?text=Hotel+Image',
            owner_id=current_user.id, # This links the hotel to the current user
            is_approved=False
        )
        db.session.add(new_hotel)
        db.session.commit()
        flash('Hotel registration submitted for approval!', 'success')
        return redirect(url_for('owner_dashboard'))
    return render_template('register_hotel.html')

@app.route('/owner_dashboard')
@role_required('owner')
def owner_dashboard():
    # Hotels owned by the current user
    hotels_owned = Hotel.query.filter_by(owner_id=current_user.id).all()
    
    # Get bookings for the hotels owned by the current user
    hotel_ids = [hotel.id for hotel in hotels_owned]
    bookings_for_my_hotels = Booking.query.filter(Booking.hotel_id.in_(hotel_ids)).order_by(Booking.check_in_date.desc()).all()
    
    return render_template('owner_dashboard.html', hotels=hotels_owned, bookings=bookings_for_my_hotels)

# --- Admin Routes ---
@app.route('/admin_dashboard')
@role_required('admin')
def admin_dashboard():
    all_users = User.query.all()
    all_hotels = Hotel.query.order_by(Hotel.is_approved.asc(), Hotel.id.desc()).all()
    all_bookings = Booking.query.order_by(Booking.id.desc()).all()
    return render_template('admin_dashboard.html', users=all_users, hotels=all_hotels, bookings=all_bookings)

@app.route('/admin/approve_hotel/<int:hotel_id>', methods=['POST'])
@role_required('admin')
def approve_hotel(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    hotel.is_approved = True
    db.session.commit()
    flash(f'Hotel "{hotel.name}" has been approved.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject_hotel/<int:hotel_id>', methods=['POST'])
@role_required('admin')
def reject_hotel(hotel_id):
    hotel = Hotel.query.get_or_404(hotel_id)
    db.session.delete(hotel) # Or mark as rejected
    db.session.commit()
    flash(f'Hotel "{hotel.name}" has been rejected and removed.', 'warning')
    return redirect(url_for('admin_dashboard'))

# --- Create Admin User (Run once manually or via a script) ---
def create_admin_user_if_not_exists():
    with app.app_context():
        if not User.query.filter_by(username='admin').first():
            admin_user = User(username='admin', email='admin@example.com', role='admin')
            admin_user.set_password('adminpass')
            db.session.add(admin_user)
            db.session.commit()
            print("Admin user created.")
        else:
            print("Admin user already exists.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    create_admin_user_if_not_exists()
    app.run(debug=True, port=5001)
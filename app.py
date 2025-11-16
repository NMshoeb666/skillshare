from flask import Flask, render_template, request, redirect, session, url_for, flash
import mysql.connector
from config import db_config
from utils import calculate_points, match_score
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey'

def get_db():
    return mysql.connector.connect(**db_config)

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        db = get_db(); cursor = db.cursor()
        try:
            cursor.execute("INSERT INTO users (name,email,password,points) VALUES (%s,%s,%s,100)", (name,email,password)) # Start with 100 points
            db.commit()
            flash('Account created. Please login.','success')
            return redirect(url_for('login'))
        except mysql.connector.IntegrityError:
            flash('Email already registered.','danger')
        finally:
            cursor.close(); db.close()
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']; password = request.form['password']
        db = get_db(); cursor = db.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE email=%s AND password=%s", (email,password))
        user = cursor.fetchone(); cursor.close(); db.close()
        if user:
            session['user_id'] = user['user_id']; session['name'] = user['name']
            return redirect(url_for('dashboard'))
        flash('Invalid credentials','danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT user_id, name, email, points FROM users WHERE user_id=%s", (uid,))
    user = cursor.fetchone()
    cursor.execute("SELECT * FROM skills WHERE user_id=%s", (uid,))
    my_skills = cursor.fetchall()
    
    # Updated query to get all matches (booked, completed, etc.)
    cursor.execute("""SELECT m.*, s.skill_name, u.name as teacher_name, ul.name as learner_name
                      FROM matches m
                      JOIN skills s ON m.skill_id=s.skill_id
                      JOIN users u ON m.teacher_id=u.user_id
                      JOIN users ul ON m.learner_id=ul.user_id
                      WHERE m.teacher_id=%s OR m.learner_id=%s
                      ORDER BY m.created_at DESC LIMIT 10""", (uid, uid))
    matches = cursor.fetchall()
    cursor.close(); db.close()
    return render_template('dashboard.html', user=user, skills=my_skills, matches=matches)

@app.route('/add_skill', methods=['GET','POST'])
def add_skill():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form['skill_name']
        description = request.form.get('description','')
        difficulty = request.form['difficulty'].lower()
        rarity = request.form['rarity'].lower()
        # NEW: Get availability
        availability = request.form.get('availability', '') 
        uid = session['user_id']
        earn, spend = calculate_points(difficulty, rarity)
        
        db = get_db(); cursor = db.cursor()
        # NEW: Insert availability into the DB
        cursor.execute("""INSERT INTO skills 
                       (user_id,skill_name,description,difficulty,rarity,points_earn,points_spend,availability) 
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                       (uid,name,description,difficulty,rarity,earn,spend,availability))
        db.commit(); cursor.close(); db.close()
        flash('Skill added','success'); return redirect(url_for('dashboard'))
    return render_template('add_skill.html')

@app.route('/explore')
@app.route('/explore', methods=['GET', 'POST'])
def explore():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor(dictionary=True)

    if request.method == 'POST':
        query = request.form['query'].strip()
        cursor.execute("""
            SELECT s.*, u.name AS teacher,
                   ROUND(AVG(f.rating), 1) AS trust_score
            FROM skills s
            JOIN users u ON s.user_id = u.user_id
            LEFT JOIN matches m ON m.skill_id = s.skill_id
            LEFT JOIN feedback f ON f.match_id = m.match_id
            WHERE s.skill_name LIKE %s AND s.user_id != %s
            GROUP BY s.skill_id
            ORDER BY s.points_earn DESC
        """, (f"%{query}%", session['user_id']))
    else:
        cursor.execute("""
            SELECT s.*, u.name AS teacher,
                   ROUND(AVG(f.rating), 1) AS trust_score
            FROM skills s
            JOIN users u ON s.user_id = u.user_id
            LEFT JOIN matches m ON m.skill_id = s.skill_id
            LEFT JOIN feedback f ON f.match_id = m.match_id
            WHERE s.user_id != %s
            GROUP BY s.skill_id
            ORDER BY s.points_earn DESC
        """, (session['user_id'],))

    skills = cursor.fetchall()
    cursor.close()
    db.close()
    return render_template('explore.html', skills=skills)

# NEW ROUTE (replaces old /learn logic)
@app.route('/schedule_session/<int:skill_id>', methods=['GET'])
def schedule_session(skill_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    learner_id = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)

    # Fetch skill, teacher, and availability
    cursor.execute("""SELECT s.*, u.name as teacher_name 
                      FROM skills s JOIN users u ON s.user_id = u.user_id 
                      WHERE s.skill_id = %s""", (skill_id,))
    skill = cursor.fetchone()

    # Fetch learner's points
    cursor.execute("SELECT points FROM users WHERE user_id = %s", (learner_id,))
    learner_points = cursor.fetchone()['points']

    if not skill:
        flash('Skill not found','danger'); cursor.close(); db.close()
        return redirect(url_for('explore'))

    if skill['user_id'] == learner_id:
        flash('Cannot learn your own skill','danger'); cursor.close(); db.close()
        return redirect(url_for('explore'))

    # Check if learner has enough points (cost is points_spend)
    cost = skill['points_spend']
    if learner_points < cost:
        flash(f'Not enough points. You need {cost}, but you only have {learner_points}.','danger')
        cursor.close(); db.close()
        return redirect(url_for('explore'))

    # Check if already booked or completed
    cursor.execute("""SELECT * FROM matches 
                      WHERE learner_id = %s AND skill_id = %s AND (status = 'booked' OR status = 'completed')""", 
                   (learner_id, skill_id))
    existing = cursor.fetchone()
    if existing:
        flash(f'You have already {existing["status"]} this skill.','warning')
        cursor.close(); db.close()
        return redirect(url_for('dashboard'))

    # Parse availability slots
    slots = []
    if skill.get('availability'):
        slots = [s.strip() for s in skill['availability'].split(',') if s.strip()]

    cursor.close(); db.close()
    return render_template('schedule_session.html', skill=skill, slots=slots, learner_points=learner_points)

# NEW ROUTE (handles booking)
@app.route('/book_session/<int:skill_id>', methods=['POST'])
def book_session(skill_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    learner_id = session['user_id']
    selected_slot = request.form.get('selected_slot')

    if not selected_slot:
        flash('You must select a slot.','danger')
        return redirect(url_for('schedule_session', skill_id=skill_id))

    db = get_db(); cursor = db.cursor(dictionary=True)
    
    # Get skill info again for security
    cursor.execute("SELECT * FROM skills WHERE skill_id = %s", (skill_id,))
    skill = cursor.fetchone()
    
    # Get learner points again for security
    cursor.execute("SELECT points FROM users WHERE user_id = %s", (learner_id,))
    learner_points = cursor.fetchone()['points']

    # Check for skill existence before trying to access it
    if not skill:
        flash('Skill not found','danger'); cursor.close(); db.close()
        return redirect(url_for('explore'))
        
    teacher_id = skill['user_id']
    earn_points = skill['points_earn']
    spend_points = skill['points_spend'] # This is the cost

    # --- Re-run all checks ---
    if teacher_id == learner_id:
        flash('Cannot learn your own skill','danger'); cursor.close(); db.close()
        return redirect(url_for('explore'))
        
    if learner_points < spend_points:
        flash('Not enough points.','danger'); cursor.close(); db.close()
        return redirect(url_for('explore'))

    cursor.execute("SELECT * FROM matches WHERE learner_id = %s AND skill_id = %s AND (status = 'booked' OR status = 'completed')", (learner_id, skill_id))
    if cursor.fetchone():
        flash('You have already booked or learned this skill.','warning'); cursor.close(); db.close()
        return redirect(url_for('dashboard'))
    # --- End Checks ---

    # 1. Transfer points
    cursor.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (earn_points, teacher_id))
    cursor.execute("UPDATE users SET points = points + %s WHERE user_id = %s", (spend_points, learner_id))

    # 2. Create match record
    #
    #    vvvvvvvvv THIS IS THE CRITICAL LINE vvvvvvvvv
    #
    #    'booked' is a hardcoded value for the 'status' column.
    #    %s is the placeholder for the 'selected_slot' variable.
    #
    cursor.execute("""INSERT INTO matches 
                   (teacher_id, learner_id, skill_id, status, booked_slot, created_at) 
                   VALUES (%s, %s, %s, 'booked', %s, CURRENT_TIMESTAMP)""", 
                   (teacher_id, learner_id, skill_id, selected_slot))
    #
    #   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    #

    db.commit(); cursor.close(); db.close()
    flash('Session booked! Points transferred.','success')
    return redirect(url_for('dashboard'))


@app.route('/search_matches', methods=['POST'])
def search_matches():
    # This route appears to be for a different feature (matching)
    # Keeping it as-is
    if 'user_id' not in session: return redirect(url_for('login'))
    q = request.form['q'].strip()
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT s.*, u.name as teacher FROM skills s JOIN users u ON s.user_id=u.user_id WHERE s.skill_name LIKE %s AND s.user_id<>%s",
                   (f"%{q}%", uid))
    candidates = cursor.fetchall()
    for c in candidates:
        c['score'] = match_score(c) # Assumes match_score from utils
    candidates.sort(key=lambda x: x['score'], reverse=True)
    cursor.close(); db.close()
    return render_template('match.html', matches=candidates, query=q)

@app.route('/feedback/<int:match_id>', methods=['GET','POST'])
def feedback(match_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    # Check if user is the learner for this match
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM matches WHERE match_id = %s AND learner_id = %s", (match_id, session['user_id']))
    match = cursor.fetchone()
    
    if not match:
        flash('Match not found or you are not authorized to give feedback.','danger')
        cursor.close(); db.close()
        return redirect(url_for('dashboard'))

    if match['status'] == 'completed':
        flash('Feedback already submitted for this session.','warning')
        cursor.close(); db.close()
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        rating = int(request.form['rating']); comment = request.form.get('comment','')
        
        # Insert feedback
        cursor.execute("INSERT INTO feedback (match_id,rating,comment) VALUES (%s,%s,%s)", (match_id,rating,comment))
        
        # NEW: Update match status to 'completed'
        cursor.execute("UPDATE matches SET status = 'completed' WHERE match_id = %s", (match_id,))
        
        db.commit(); cursor.close(); db.close()
        flash('Thanks for feedback! Session marked as complete.','success')
        return redirect(url_for('dashboard'))
        
    cursor.close(); db.close()
    return render_template('feedback.html', match_id=match_id)

if __name__ == '__main__':
    app.run(debug=True)

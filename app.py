from flask import Flask, render_template, request, redirect, session, url_for, flash
import mysql.connector
from config import db_config
from utils import calculate_points, match_score
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey'

def get_db():
    return mysql.connector.connect(**db_config)

# Home / Index
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Register
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        db = get_db(); cursor = db.cursor()
        try:
            cursor.execute("INSERT INTO users (name,email,password,points) VALUES (%s,%s,%s,0)", (name,email,password))
            db.commit()
            flash('Account created. Please login.','success')
            return redirect(url_for('login'))
        except mysql.connector.IntegrityError:
            flash('Email already registered.','danger')
        finally:
            cursor.close(); db.close()
    return render_template('register.html')

# Login
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

# Logout
@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))

# Dashboard
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT user_id, name, email, points FROM users WHERE user_id=%s", (uid,))
    user = cursor.fetchone()
    cursor.execute("SELECT * FROM skills WHERE user_id=%s", (uid,))
    my_skills = cursor.fetchall()
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

# Add skill
@app.route('/add_skill', methods=['GET','POST'])
def add_skill():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        name = request.form['skill_name']
        description = request.form.get('description','')
        difficulty = request.form['difficulty'].lower()
        rarity = request.form['rarity'].lower()
        uid = session['user_id']
        earn, spend = calculate_points(difficulty, rarity)
        db = get_db(); cursor = db.cursor()
        cursor.execute("INSERT INTO skills (user_id,skill_name,description,difficulty,rarity,points_earn,points_spend) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                       (uid,name,description,difficulty,rarity,earn,spend))
        db.commit(); cursor.close(); db.close()
        flash('Skill added','success'); return redirect(url_for('dashboard'))
    return render_template('add_skill.html')

# Explore all skills
@app.route('/explore')
def explore():
    if 'user_id' not in session: return redirect(url_for('login'))
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT s.*, u.name as teacher FROM skills s JOIN users u ON s.user_id = u.user_id ORDER BY s.points_earn DESC")
    skills = cursor.fetchall(); cursor.close(); db.close()
    return render_template('explore.html', skills=skills)

# Search & match scoring
@app.route('/search_matches', methods=['POST'])
def search_matches():
    if 'user_id' not in session: return redirect(url_for('login'))
    q = request.form['q'].strip()
    uid = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT s.*, u.name as teacher FROM skills s JOIN users u ON s.user_id=u.user_id WHERE s.skill_name LIKE %s AND s.user_id<>%s",
                   (f"%{q}%", uid))
    candidates = cursor.fetchall()
    for c in candidates:
        c['score'] = match_score(c)
    candidates.sort(key=lambda x: x['score'], reverse=True)
    cursor.close(); db.close()
    return render_template('match.html', matches=candidates, query=q)

# Create match request
@app.route('/create_match/<int:skill_id>', methods=['POST'])
def create_match(skill_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    learner_id = session['user_id']
    db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM skills WHERE skill_id=%s", (skill_id,))
    skill = cursor.fetchone()
    if not skill:
        flash('Skill not found','danger'); cursor.close(); db.close(); return redirect(url_for('explore'))
    teacher_id = skill['user_id']
    if teacher_id == learner_id:
        flash('Cannot learn your own skill','danger'); cursor.close(); db.close(); return redirect(url_for('explore'))
    cursor.execute("INSERT INTO matches (teacher_id,learner_id,skill_id,status) VALUES (%s,%s,%s,'pending')", (teacher_id, learner_id, skill_id))
    db.commit(); cursor.close(); db.close()
    flash('Match request created','success'); return redirect(url_for('dashboard'))

# Complete match and transfer points
@app.route('/complete_match/<int:match_id>', methods=['POST'])
def complete_match(match_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    uid = session['user_id']; db = get_db(); cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM matches WHERE match_id=%s", (match_id,))
    m = cursor.fetchone()
    if not m:
        flash('Match not found','danger'); cursor.close(); db.close(); return redirect(url_for('dashboard'))
    if uid not in (m['teacher_id'], m['learner_id']):
        flash('Not authorized','danger'); cursor.close(); db.close(); return redirect(url_for('dashboard'))
    cursor.execute("SELECT points_earn, points_spend FROM skills WHERE skill_id=%s", (m['skill_id'],))
    p = cursor.fetchone()
    cursor.execute("UPDATE users SET points = points + %s WHERE user_id=%s", (p['points_earn'], m['teacher_id']))
    cursor.execute("UPDATE users SET points = points + %s WHERE user_id=%s", (p['points_spend'], m['learner_id']))
    cursor.execute("UPDATE matches SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE match_id=%s", (match_id,))
    db.commit(); cursor.close(); db.close()
    flash('Match completed and points transferred','success'); return redirect(url_for('dashboard'))

# Feedback
@app.route('/feedback/<int:match_id>', methods=['GET','POST'])
def feedback(match_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        rating = int(request.form['rating']); comment = request.form.get('comment','')
        db = get_db(); cursor = db.cursor()
        cursor.execute("INSERT INTO feedback (match_id,rating,comment) VALUES (%s,%s,%s)", (match_id,rating,comment))
        db.commit(); cursor.close(); db.close()
        flash('Thanks for feedback!','success'); return redirect(url_for('dashboard'))
    return render_template('feedback.html', match_id=match_id)

if __name__ == '__main__':
    app.run(debug=True)

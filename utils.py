def calculate_points(difficulty, rarity):
    base_earn = {'easy': 5, 'moderate': 10, 'difficult': 20}
    base_spend = {'easy': -5, 'moderate': -10, 'difficult': -20}
    rarity_bonus = {'common': 0, 'uncommon': 3, 'rare': 5}
    earn = base_earn[difficulty] + rarity_bonus[rarity]
    spend = base_spend[difficulty] - rarity_bonus[rarity]
    return earn, spend

def match_score(skill):
    # skill: dict containing points_earn, difficulty, rarity, optional teacher_rating
    score = 0
    score += skill.get('points_earn', 0)
    rarity_w = {'common': 0, 'uncommon': 5, 'rare': 10}
    score += rarity_w.get(skill.get('rarity','common'), 0)
    diff_w = {'easy': 0, 'moderate': 5, 'difficult': 10}
    score += diff_w.get(skill.get('difficulty','easy'), 0)
    if skill.get('teacher_rating') is not None:
        score += skill['teacher_rating'] * 2
    return score

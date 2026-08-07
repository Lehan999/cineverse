from app import db

class Movie(db.Model):

    id=db.Column(db.Integer, primary_key=True)

    title=db.Column(db.String(100), nullable=False)
    genre=db.Column(db.String(100))
    language=db.Column(db.String(50))
    release_year=db.Column(db.Integer)
    duration=db.Column(db.String(20))

    imdb_rating=db.Column(db.Float)

    description=db.Column(db.Text)


    poster=db.Column(db.String(200))
    trailer=db.Column(db.String(300))

class Cast(db.Model):
    id=db.Column(db.Integer, primary_key=True)

    movie_id=db.Column(db.Integer,db.ForeignKey('movie.id'))

    actor_name=db.Column(db.String(100), nullable=False)
    character_name=db.Column(db.String(100))
    photo=db.Column(db.String(200))

class Crew(db.Model):
    id=db.Column(db.Integer, primary_key=True)

    movie_id=db.Column(db.Integer,db.ForeignKey('movie.id'))

    name=db.Column(db.String(100), nullable=False)
    role=db.Column(db.String(50))
    photo=db.Column(db.String(200))
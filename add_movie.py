from app import create_app,db
from app.models import Movie

app=create_app()

with app.app_context():

    movie=Movie(
        title="Avengers Endgame",
        genre="Action Adventure Sci-Fi",
        language="English",
        release_year=2019,
        duration="3h 1min",
        imdb_rating=8.4,
        description="After the devastating events of Infinity war, the remaining Avengers unite for one final battle to restore the universe.",
        poster="images/avengers_endgame.png",
        trailer="https://www.youtube.com/watch?v=TcMBFSGVi1c"
    )

    db.session.add(movie)
    db.session.commit()

print("Movie added sucessfully!")
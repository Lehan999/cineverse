from flask import Blueprint,render_template
from app.models import Movie

main=Blueprint("main",__name__)

@main.route("/")

def home():
    movies=Movie.query.all()
    return render_template("home.html",movies=movies)

@main.route("/movie/<int:id>")

def movie(id):
    movie=Movie.query.get_or_404(id)
    return render_template("movie.html",movie=movie)
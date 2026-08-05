from flask import Blueprint,render_template

main=Blueprint("main",__name__)

@main.route("/")

def home():
    return render_template("home.html")

@main.route("/movie")

def movie():
    return render_template("movie.html")
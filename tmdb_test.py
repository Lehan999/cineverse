from app import create_app
from app.tmdb import TMDBError, popular_movies


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        try:
            movies = popular_movies()
        except TMDBError as error:
            print("TMDB check failed:", error)
        else:
            print("TMDB returned", len(movies), "popular movies.")
            if movies:
                print("First movie:", movies[0]["title"])

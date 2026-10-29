from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, EqualTo, InputRequired, Length, NumberRange, Regexp


class RegistrationForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(min=3, max=32),
            Regexp(r"^[A-Za-z0-9_.-]+$", message="Use letters, numbers, dots, dashes, or underscores."),
        ],
    )
    email = StringField(
        "Email",
        validators=[
            DataRequired(),
            Length(max=254),
            Regexp(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", message="Enter a valid email address."),
        ],
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=8, max=128)],
    )
    confirm_password = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    submit = SubmitField("Create account")


class LoginForm(FlaskForm):
    identifier = StringField("Username or email", validators=[DataRequired(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    submit = SubmitField("Sign in")


class ReviewForm(FlaskForm):
    rating = IntegerField(
        "Your rating (1–10)",
        validators=[InputRequired(), NumberRange(min=1, max=10, message="Choose a rating from 1 to 10.")],
    )
    review_text = TextAreaField("Review (optional)", validators=[Length(max=2000)])
    submit = SubmitField("Save rating and review")

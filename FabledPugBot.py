import os
import random
from discord.ext import commands
import discord
import sqlite3
import json
from discord import Object
import names
import asyncio
import math
import uuid


#VARS
DEFAULT_ELO = 1000

#SQL
class User:
    def __init__(self, id):
        self.id = id
        self.elo = {}

    def get_elo(self, game):
        if game not in self.elo:
            self.elo[game] = 1200
        return self.elo[game]

    def update_elo(self, game, new_elo):
        self.elo[game] = new_elo
        save_user(self)

    def to_dict(self):
        return {"id": self.id, "elo": self.elo}

    @classmethod
    def from_dict(cls, data):
        user = cls(data["id"])
        user.elo = data["elo"]
        return user

class FakeUser:
    def __init__(self, id, name="FakeUser", elo=1000):
        self.id = id
        self.name = name
        self.elo = elo


    @property
    def mention(self):
        return f"@{self.name}"


conn = sqlite3.connect('elo.db')
cursor = conn.cursor()
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS user_elo (
        user_id INTEGER NOT NULL,
        game TEXT NOT NULL,
        elo INTEGER NOT NULL DEFAULT 1200,
        in_queue BOOLEAN NOT NULL DEFAULT 0,
        in_match BOOLEAN NOT NULL DEFAULT 0,
        match_id INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, game)
    )
    """
)
#cursor.execute("ALTER TABLE user_elo ADD COLUMN in_match INTEGER NOT NULL DEFAULT 0")
#cursor.execute("ALTER TABLE user_elo ADD COLUMN in_queue INTEGER NOT NULL DEFAULT 0")
#cursor.execute("ALTER TABLE user_elo ADD COLUMN match_id TEXT")
conn.commit()
conn.close()

#defs

def generate_unique_match_id():
    return str(uuid.uuid4())

def create_balanced_teams(players, game):
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()
    elo_ratings = {}
    for player in players:
        cursor.execute("SELECT elo FROM user_elo WHERE user_id = ? AND game = ?", (player.id, game))
        result = cursor.fetchone()
        elo_ratings[player.id] = result[0] if result else DEFAULT_ELO

    sorted_players = sorted(players, key=lambda p: elo_ratings[p.id], reverse=True)

    red_team = sorted_players[::2]
    blue_team = sorted_players[1::2]

    cursor.close()
    conn.close()

    return red_team, blue_team


def create_teams(queue, game):
    # Open the connection to the database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Set default Elo rating
    default_elo = 1200

    # Check each player's Elo rating in the database and add to a list of tuples
    player_elos = []
    for player in queue:
        cursor.execute("SELECT elo FROM user_elo WHERE user_id = ? AND game = ?", (player.id, game))
        result = cursor.fetchone()
        if result is None:
            player_elos.append((player, default_elo))
        else:
            player_elos.append((player, result[0]))

    # Shuffle the queue and divide into red and blue teams
    random.shuffle(player_elos)
    red_team = [player_elo[0] for player_elo in player_elos[:len(player_elos)//2]]
    blue_team = [player_elo[0] for player_elo in player_elos[len(player_elos)//2:]]

    # Close the connection to the database
    cursor.close()
    conn.close()

    return red_team, blue_team

def update_elo(red_team, blue_team, conn):
    k = 32
    red_team_elo = 0
    blue_team_elo = 0

    # Calculate the total ELO for the red team
    for player in red_team:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM user_elo WHERE user_id=?", (player.id,))
        result = cursor.fetchone()
        if result:
            red_team_elo += result[0]
        else:
            # If the player is not found in the database, assume their ELO is 1000
            red_team_elo += 1000

    # Calculate the total ELO for the blue team
    for player in blue_team:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM user_elo WHERE user_id=?", (player.id,))
        result = cursor.fetchone()
        if result:
            blue_team_elo += result[0]
        else:
            # If the player is not found in the database, assume their ELO is 1000
            blue_team_elo += 1000

    # Calculate expected scores
    expected_winner = 1 / (1 + 10**((blue_team_elo - red_team_elo) / 400))
    expected_loser = 1 / (1 + 10**((red_team_elo - blue_team_elo) / 400))

    # Update Elo ratings
    for player in red_team:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM user_elo WHERE user_id=?", (player.id,))
        result = cursor.fetchone()
        if result:
            elo = result[0]
        else:
            elo = 1000
        elo += round(k * (0 - expected_loser))
        cursor.execute("UPDATE user_elo SET elo=? WHERE user_id=?", (elo, player.id))

    for player in blue_team:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM user_elo WHERE user_id=?", (player.id,))
        result = cursor.fetchone()
        if result:
            elo = result[0]
        else:
            elo = 1000
        elo += round(k * (1 - expected_winner))
        cursor.execute("UPDATE user_elo SET elo=? WHERE user_id=?", (elo, player.id))

    # Commit changes and close the cursor
    conn.commit()




def calculate_elo_difference(winner_elo, loser_elo, result):
    k_factor = 32
    expected_outcome = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    elo_diff = round(k_factor * (result - expected_outcome))
    return elo_diff

def get_teams_from_match_id(match_id, game):
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id FROM user_elo WHERE match_id = ? AND game = ?", (match_id, game))
    player_ids = [row[0] for row in cursor.fetchall()]
    
    cursor.close()
    conn.close()
    
    players = [bot.get_user(player_id) for player_id in player_ids]
    return players[:len(players) // 2], players[len(players) // 2:]    

def remove_players_from_match(match_id, players, conn):
    cursor = conn.cursor()

    for player in players:
        cursor.execute("UPDATE user_elo SET match_id = 0, in_match = 0, in_queue = 0 WHERE user_id = ?", (player.id,))

    cursor.execute("UPDATE user_elo SET match_id = 0 WHERE match_id = ?", (match_id,))
    conn.commit()
    cursor.close()

def format_queue(game):
    queue_list = [player.mention for player in game_queues[game]["queue"]]
    if len(queue_list) == 0:
        return "Queue is empty."
    return '\n'.join(queue_list)

def get_user_queue_game(user_id, conn):
    cursor = conn.cursor()
    cursor.execute("SELECT game FROM user_elo WHERE user_id = ? AND in_queue = 1", (user_id,))
    result = cursor.fetchone()
    if result:
        return result[0]
    else:
        return None

#ARRAYS
game_queues = {
    "Splitgate": {
        "queue": [],
        "game_modes": ['KOTH', 'DOM', 'TDM'],
        "pugsize": 8,
        "maps": ['Abyss','Atlantis','Crag','Club Silo','Foregone Destruction','Helix','Highwind','Karman Station','Impact','Lavawell','Oasis','Olympus','Pantheon','Stadium'],
        "map_images": {
            "Abyss": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Abyss.png",
            "Atlantis": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Atlantis.png",
            "Crag": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Crag.png",
            "Club Silo": r"C:/Users/Reitz/Pictures/Splitgate_Maps/Club Silo.png",
            "Foregone Destruction": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Foregone Destruction.png",
            "Helix": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Helix.png",
            "Highwind": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Highwind.png",
            "Karman Station": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Karman station.png",
            "Impact": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Impact.png",
            "Lavawell": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Lavawell.png",
            "Oasis": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Oasis.png",
            "Olympus": r"C:/Users/Reitz/Pictures/Splitgate_Maps/Olympus.png",
            "Pantheon": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Pantheon.png",
            "Stadium": r"C:\Users\Reitz\Pictures\Splitgate_Maps\Stadium.png"
        }
    },
    "Overwatch": {
        "queue": [],
        "game_modes": ['Payload', 'Complain', 'Cry'],
        "pugsize": 10,
        "maps": ['Hanamura','Horizon Lunar Colony','Paris','Temple of Anubis','Dorado'],
        "map_images": {
            "Hanamura": r"C:\Users\Reitz\Pictures\DuckWithBread.jpg",
            "Horizon Lunar Colony": r"C:\Users\Reitz\Pictures\DuckWithBread.jpg",
            "Paris": r"C:\Users\Reitz\Pictures\DuckWithBread.jpg",
            "Temple of Anubis": r"C:\Users\Reitz\Pictures\DuckWithBread.jpg",
            "Dorado": r"C:\Users\Reitz\Pictures\DuckWithBread.jpg"
        }
    }
}

    # Add more maps and their corresponding local file paths


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

queue = []
elo_ratings = {}
maps = ['Map1', 'Map2', 'Map3', 'Map4', 'Map5']

@bot.event
async def on_ready():
    # Connect to the database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Set all users' in_queue and in_match values to 0
    cursor.execute("UPDATE user_elo SET in_queue = 0, in_match = 0")

    # Commit the changes and close the cursor and connection
    conn.commit()
    cursor.close()
    conn.close()

    # Print a message to indicate that the bot is online and the database has been updated
    print(f'{bot.user} has connected to Discord and updated the database.')



#commands
@bot.command(name="queue")
async def queue(ctx):
    conn = sqlite3.connect("elo.db")
    cursor = conn.cursor()

    # Get all users in queue
    cursor.execute("SELECT user_id FROM user_elo WHERE in_queue = 1")
    rows = cursor.fetchall()

    # If no users are in queue, send a message saying so
    if not rows:
        await ctx.send("No users are currently in queue.")
        return

    # Create a list of mentions for all users in queue
    mentions = [f"<@{row[0]}>" for row in rows]

    # Send a message with the list of mentions
    queue_message = "Players in queue:\n" + "\n".join(mentions)
    await ctx.send(queue_message)

    cursor.close()
    conn.close()



#JOIN

@bot.command(name="join")
async def join(ctx, game: str):
    game = game.capitalize()
    game_key = next((g for g in game_queues if g.lower() == game.lower()), None)

    if not game_key:
        await ctx.send(f"{game} is not a valid game.")
        return

    user = ctx.author

    # Connect to the SQLite database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Check if user exists in the database and their queue/match status
    cursor.execute("SELECT elo, in_queue, in_match FROM user_elo WHERE user_id = ? AND game = ?", (user.id, game))
    result = cursor.fetchone()

    if not result:
        # If user doesn't exist, add them to the database with default ELO rating and not in queue/match
        cursor.execute("INSERT INTO user_elo (user_id, game, elo, in_queue, in_match, match_id) VALUES (?, ?, ?, ?, ?, ?)", (user.id, game, 1200, False, False, 0))
        conn.commit()
        elo_ratings[user.id] = 1200
        elo = 1200
        in_queue = 0
        in_match = False
    else:
        # If user exists, load their ELO rating and queue/match status from the database
        elo, in_queue, in_match = result
        elo_ratings[user.id] = elo

    if in_queue != 0:
        await ctx.send(f"{user.mention}, you are already in the queue for {game}.")
        return

    if in_match != 0:
        await ctx.send(f"{user.mention}, you are currently in a match for {game}. Please finish the match before joining a new queue.")
        return

    # Update user's in_queue status
    cursor.execute("UPDATE user_elo SET in_queue = ? WHERE user_id = ? AND game = ?", (True, user.id, game))
    conn.commit()

    game_queues[game_key]["queue"].append(user)

    if len(game_queues[game_key]["queue"]) < 8:
        await ctx.send(f'{user.mention} has joined the queue for {game} (ELO: {elo}).\nCurrent queue: {format_queue(game_key)}')
    else:
        # Create teams
        red_team, blue_team = create_teams(game_queues[game_key]["queue"], game)

        # Assign a match_id to all players in the match
        match_id = generate_unique_match_id()
        cursor.executemany("UPDATE user_elo SET in_queue = 1, in_match = 1, match_id = ? WHERE user_id = ?", [(match_id, player.id) for player in red_team + blue_team])
        conn.commit()

        # Select 5 random maps and game modes
        selected_maps = random.sample(list(game_queues[game_key]["map_images"].keys()), 5)
        selected_game_modes = random.sample(game_queues[game_key]["game_modes"], 5)

        # Create a rich embed message with the teams and game modes
        embed = discord.Embed(title=f"Match Starting ({game})", description=f"**Red Team:**\n{', '.join([player.mention for player in red_team])}\n\n**Blue Team:**\n{', '.join([player.mention for player in blue_team])}\n\n**Game Modes:**\n{', '.join(selected_game_modes)}")
        await ctx.send(embed=embed)

        # Send map embeds
        for index, map_name in enumerate(selected_maps, start=1):
            map_image_path = game_queues[game_key]["map_images"][map_name]
            embed = discord.Embed(title=f"Map {index}: {map_name}")
            map_image_file = discord.File(map_image_path, filename=f"map_image_{index}.png")
            embed.set_image(url=f"attachment://map_image_{index}.png")
            await ctx.send(embed=embed, file=map_image_file)

        # Clear the queue
        game_queues[game_key]["queue"].clear()

    # Close the database connection
    cursor.close()
    conn.close()


# leaderboard
@bot.command(name="leaderboard")
async def leaderboard(ctx, game: str):
    game = game.capitalize()

    # Connect to the SQLite database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Retrieve the top 10 players' ELO ratings from the database
    cursor.execute("SELECT user_id, elo FROM user_elo WHERE game = ? ORDER BY elo DESC LIMIT 10", (game,))
    results = cursor.fetchall()

    # Create an embed to display the leaderboard
    embed = discord.Embed(title=f"Top 10 {game} players by ELO")
    for i, (user_id, elo) in enumerate(results, start=1):
        user = await bot.fetch_user(user_id)
        embed.add_field(name=f"{i}. {user.name}", value=f"ELO: {elo}", inline=False)

    # Send the embed to the channel where the command was called
    await ctx.send(embed=embed)

    # Close the database connection
    cursor.close()
    conn.close()



#LEAVE

@bot.command(name="leave")
async def leave(ctx, game: str):
    game = game.capitalize()
    game_key = next((g for g in game_queues if g.lower() == game.lower()), None)

    if not game_key:
        await ctx.send(f"{game} is not a valid game.")
        return

    user = ctx.author

    # Connect to the SQLite database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Check if the user is in the queue
    cursor.execute("SELECT in_queue FROM user_elo WHERE user_id = ? AND game = ?", (user.id, game))
    result = cursor.fetchone()

    if not result or not result[0]:
        await ctx.send(f"{user.mention}, you are not in the queue for {game}.")
        return

    # Update user's in_queue status
    cursor.execute("UPDATE user_elo SET in_queue = ? WHERE user_id = ? AND game = ?", (False, user.id, game))
    conn.commit()

    game_queues[game_key]["queue"].remove(user)
    await ctx.send(f"{user.mention} has left the queue for {game}.\nCurrent queue: {format_queue(game_key)}")

    # Close the database connection
    cursor.close()
    conn.close()


#simulate

@bot.command(name="simulate")
async def simulate(ctx, game):
    if game not in game_queues:
        await ctx.send(f"No queue found for {game}")
        return

    # Set the required number of players for each game
    required_players = {"Splitgate": 8, "Overwatch": 10}

    # Add fake users to the queue to reach the required number of players
    while len(game_queues[game]["queue"]) < required_players[game]:
        fake_id = random.randint(100000000000000000, 999999999999999999)
        fake_name = names.get_full_name()  # Generate a random name
        fake_user = FakeUser(name=fake_name, id=fake_id)
        game_queues[game]["queue"].append(fake_user)

    # Create balanced teams and display match starting embed
    red_team, blue_team = create_balanced_teams(game_queues[game]["queue"], game)

    red_team_mentions = [player.mention for player in red_team]
    blue_team_mentions = [player.mention for player in blue_team]

    embed = discord.Embed(
        title=f"Match Starting ({game})",
        description=f"**Red Team:**\n{', '.join(red_team_mentions)}\n\n**Blue Team:**\n{', '.join(blue_team_mentions)}")
    await ctx.send(embed=embed)

    # Display the map pictures and their selected game modes
    if game == "Splitgate":
        selected_maps = random.sample(list(game_queues[game]["map_images"].keys()), 5)
        selected_game_modes = [random.choice(game_queues[game]["game_modes"]) for _ in range(5)]

        for index, (map_name, game_mode) in enumerate(zip(selected_maps, selected_game_modes), start=1):
            map_image_path = game_queues[game]["map_images"][map_name]
            map_image_file = discord.File(map_image_path, filename=f"map_image_{index}.png")
            embed = discord.Embed(title=f"Map {index}: {map_name} ({game_mode})")
            embed.set_image(url=f"attachment://map_image_{index}.png")
            await ctx.send(embed=embed, file=map_image_file)

    # Update the players' game and match data
    match_id = f"{game}-{ctx.message.id}"
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    for player in red_team + blue_team:
        cursor.execute("UPDATE user_elo SET in_queue = 1, in_match = 1, match_id = ? WHERE user_id = ? AND game = ?", (match_id, player.id, game))
        conn.commit()

    cursor.close()
    conn.close()


    # Remove the players from the queue
    #game_queues[game]["queue"] = [player for player in game_queues[game]["queue"] if player not in red_team + blue_team]



#report buttons and stuff
class ReportView(discord.ui.View):
    def __init__(self, user_id, conn):
        super().__init__()
        self.user_id = user_id
        self.conn = conn
        self.game = get_user_queue_game(user_id, conn)

    def find_match(self, user_id):
        cursor = self.conn.cursor()

        # Select the match ID and game for the given user that is currently in a match
        cursor.execute("SELECT match_id, game FROM user_elo WHERE user_id = ? AND in_match = 1 AND game = ?", (user_id, self.game))
        result = cursor.fetchone()

        if result:
            # If the user is in a match, get the match ID and game
            match_id, game = result

            # Get all the user IDs in the same match for the same game
            cursor.execute("SELECT user_id FROM user_elo WHERE match_id = ? AND game = ?", (match_id, game))
            rows = cursor.fetchall()

            # Create a list of fake users (with IDs from the database) for each player in the match
            players = [FakeUser(id=row[0]) for row in rows]

            # Use the create_balanced_teams function to split the players into two balanced teams
            red_team, blue_team = create_balanced_teams(players, game)

            # Create a dictionary with the red team, blue team, and game for the match
            match_info = {
                "red_team": red_team,
                "blue_team": blue_team,
                "game": game
            }

            # Return the match ID and match information
            return match_id, match_info

        # If the user is not in a match, return None for both values
        return None, None

    @discord.ui.button(label="Report Win", style=discord.ButtonStyle.success)
    async def report_win_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        match_id, match = self.find_match(self.user_id)
        if not match_id:
            await interaction.response.send_message("You are not currently in a match!")
            self.stop()
        else:
            red_team = match["red_team"]
            blue_team = match["blue_team"]

            # Determine which team the user is on
            if self.user_id in red_team:
                winning_team = red_team
                losing_team = blue_team
            else:
                winning_team = blue_team
                losing_team = red_team

            # Calculate the elo changes using the update_elo function
            elo_changes = update_elo(winning_team, losing_team, self.conn)

            # Send a message indicating the win was reported successfully
            await interaction.response.send_message("You reported a win!")

            # Remove all players from the match and set their in_match status to 0
            remove_players_from_match(match_id, red_team + blue_team, self.conn)
            self.stop()


    @discord.ui.button(label="Report Loss", style=discord.ButtonStyle.danger)
    async def report_loss_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        match_id, match = self.find_match(self.user_id)
        if not match_id:
            await interaction.response.send_message("You are not currently in a match!")
            self.stop()
        else:
            red_team = match["red_team"]
            blue_team = match["blue_team"]

            # Determine which team the user is on
            if self.user_id in red_team:
                losing_team = red_team
                winning_team = blue_team
            else:
                losing_team = blue_team
                winning_team = red_team

            # Calculate the elo changes using the update_elo function
            elo_changes = update_elo(winning_team, losing_team, self.conn)

            # Send a message indicating the loss was reported successfully
            await interaction.response.send_message("You reported a loss!")

            # Remove all players from the match and set their in_match status to 0
            remove_players_from_match(match_id, red_team + blue_team, self.conn)
            self.stop()


@bot.command(name="report")
async def report(ctx):
    user_id = ctx.author.id
    conn = sqlite3.connect("elo.db")
    view = ReportView(user_id, conn)
    await ctx.send("Report your game result:", view=view)

@bot.command(name="reportwin")
async def reportwin(ctx):
    user_id = ctx.author.id
    game = "Splitgate"  # Replace with the game you want to report a win for

    conn = sqlite3.connect("elo.db")
    view = ReportView(user_id, conn)
    match_id, match = view.find_match(user_id, game)

    if not match_id:
        await ctx.send(f"{ctx.author.mention}, you are not currently in a match for {game}!")
        return

    # Report the win and send a congratulatory message
    await ctx.send(f"Congrats on the win, {ctx.author.mention}!")

    # Remove the database values for in_queue and match_id
    cursor = conn.cursor()
    cursor.execute("UPDATE user_elo SET in_queue = 0, in_match = 0, match_id = NULL WHERE user_id = ? AND game = ?", (user_id, game))
    conn.commit()
    cursor.close()
    conn.close()





#end of buttons
#set_elo

@bot.command()
@commands.has_permissions(administrator=True)
async def set_elo(ctx, user: discord.Member, game: str, new_elo: int):
    # Open the connection to the database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Check if a row exists for the user_id and game in the user_elo table
    cursor.execute("SELECT * FROM user_elo WHERE user_id = ? AND game = ?", (user.id, game))
    row = cursor.fetchone()

    if row:
        # If the row exists, update the elo value
        cursor.execute("UPDATE user_elo SET elo = ? WHERE user_id = ? AND game = ?", (new_elo, user.id, game))
        conn.commit()
        await ctx.send(f"{user.mention}'s Elo rating for {game} has been updated to {new_elo}.")
    else:
        # If the row doesn't exist, insert a new row with the specified values
        cursor.execute("INSERT INTO user_elo (user_id, game, elo) VALUES (?, ?, ?)", (user.id, game, new_elo))
        conn.commit()
        await ctx.send(f"{user.mention} has been assigned an Elo rating of {new_elo} for {game}.")

    # Close the connection to the database
    cursor.close()
    conn.close()


#get_elo

@bot.command()
async def get_elo(ctx, user: discord.Member):
    # Retrieve all the user's ELO ratings from the database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()
    cursor.execute("SELECT game, elo FROM user_elo WHERE user_id = ?", (user.id,))
    rows = cursor.fetchall()

    # Create a formatted string with all the user's ELO ratings
    if rows:
        elo_info = "\n".join([f"{row[0]}: {row[1]}" for row in rows])
        await ctx.send(f"ELO ratings for {user.mention}:\n{elo_info}")
    else:
        await ctx.send(f"No ELO ratings found for {user.mention}.")
    
    # Close the database connection
    conn.close()

#show_database

@bot.command()
@commands.has_permissions(administrator=True)
async def show_database(ctx):
    # Connect to the database
    conn = sqlite3.connect('elo.db')
    cursor = conn.cursor()

    # Get a list of all tables in the database
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()

    # Loop through each table and print its contents
    for table in tables:
        cursor.execute(f"SELECT * FROM {table[0]};")
        rows = cursor.fetchall()

        # Print the table name and contents
        table_str = f"Table: {table[0]}\n"
        for row in rows:
            table_str += f"{row}\n"
        await ctx.send(table_str)

    # Close the database connection
    cursor.close()
    conn.close()


#clear
@bot.command()
async def clear(ctx):
    global queue
    queue.clear()
    await ctx.send('The queue has been cleared.')

async def start_game():
    global queue
    team1, team2 = balance_teams()

    map_choice = random.choice(maps)
    match = {
        'team1': team1,
        'team2': team2,
        'map': map_choice,
    }

    for player in team1 + team2:
        await player.send(f'Match starting on {map_choice}\nTeam 1: {", ".join([p.mention for p in team1])}\nTeam 2: {", ".join([p.mention for p in team2])}')
    
    queue = []

def balance_teams():
    # A basic example of how to balance teams based on ELO ratings. You can improve this algorithm as needed.
    players = sorted(queue, key=lambda x: elo_ratings.get(x.id, 1200), reverse=True)
    team1 = players[::2]
    team2 = players[1::2]
    return team1, team2


# Replace 'YOUR_BOT_TOKEN' with your bot's token
bot.run('YOUR_BOT_TOKEN)
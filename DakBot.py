import asyncio
import random
import discord
from discord.ext import commands
import time
from datetime import datetime
import threading


if not discord.opus.is_loaded():
    # the 'opus' library here is opus.dll on windows
    # or libopus.so on linux in the current directory
    # you should replace this with the location the
    # opus library is located in and with the proper filename.
    # note that on windows this DLL is automatically provided for you
    discord.opus.load_opus('opus')

class VoiceEntry:
    def __init__(self, message, player):
        self.requester = message.author
        self.channel = message.channel
        self.player = player

    def __str__(self):
        fmt = '*{0.title}* uploaded by {0.uploader} and requested by {1.display_name}'
        duration = self.player.duration
        if duration:
            fmt = fmt + ' [length: {0[0]}m {0[1]}s]'.format(divmod(duration, 60))
        return fmt.format(self.player, self.requester)

class VoiceState:
    def __init__(self, bot):
        self.current = None
        self.voice = None
        self.bot = bot
        self.play_next_song = asyncio.Event()
        self.songs = asyncio.Queue()
        self.skip_votes = set() # a set of user_ids that voted
        self.audio_player = self.bot.loop.create_task(self.audio_player_task())

    def is_playing(self):
        if self.voice is None or self.current is None:
            return False

        player = self.current.player
        return not player.is_done()

    @property
    def player(self):
        return self.current.player

    def skip(self):
        self.skip_votes.clear()
        if self.is_playing():
            self.player.stop()

    def toggle_next(self):
        self.bot.loop.call_soon_threadsafe(self.play_next_song.set)

    async def audio_player_task(self):
        while True:
            self.play_next_song.clear()
            self.current = await self.songs.get()
            await self.bot.send_message(self.current.channel, 'Now playing ' + str(self.current))
            self.current.player.start()
            await self.play_next_song.wait()

class Music:


    """Voice related commands.
    Works in multiple servers at once.
    """
    def __init__(self, bot):
        self.bot = bot
        self.voice_states = {}
        self.autoplay = "on"
        self.list = []

    def get_voice_state(self, server):
        state = self.voice_states.get(server.id)
        if state is None:
            state = VoiceState(self.bot)
            self.voice_states[server.id] = state

        return state

    async def create_voice_client(self, channel):
        voice = await self.bot.join_voice_channel(channel)
        state = self.get_voice_state(channel.server)
        state.voice = voice

    """"
    def __unload(self):
        for state in self.voice_states.values():
            try:
                state.audio_player.cancel()
                if state.voice:
                    self.bot.loop.create_task(state.voice.disconnect())
            except:
                pass
    """


    @commands.command(pass_context=True, no_pm=True)
    async def join(self, ctx, *, channel : discord.Channel):
        """Joins a voice channel."""
        try:
            await self.create_voice_client(channel)
        except discord.ClientException:
            await self.bot.say('Already in a voice channel...')
        except discord.InvalidArgument:
            await self.bot.say('This is not a voice channel...')
        else:
            await self.bot.say('Ready to play audio in ' + channel.name)

    @commands.command(pass_context=True, no_pm=True)
    async def summon(self, ctx):
        """Summons the bot to join your voice channel."""
        summoned_channel = ctx.message.author.voice_channel
        if summoned_channel is None:
            await self.bot.say('You are not in a voice channel.')
            return False

        state = self.get_voice_state(ctx.message.server)
        if state.voice is None:
            state.voice = await self.bot.join_voice_channel(summoned_channel)
        else:
            await state.voice.move_to(summoned_channel)

        return True

    @commands.command(pass_context=True, no_pm=True)
    async def play(self, ctx, *, song : str):
        """Plays a song.
        If there is a song currently in the queue, then it is
        queued until the next song is done playing.
        This command automatically searches as well from YouTube.
        The list of supported sites can be found here:
        https://rg3.github.io/youtube-dl/supportedsites.html
        """
        state = self.get_voice_state(ctx.message.server)
        opts = {
            'default_search': 'auto',
            'quiet': True,
        }

        if state.voice is None:
            success = await ctx.invoke(self.summon)
            if not success:
                return

        try:
            player = await state.voice.create_ytdl_player(song, ytdl_options=opts, after=state.toggle_next)
        except Exception as e:
            fmt = 'An error occurred while processing this request: ```py\n{}: {}\n```'
            await self.bot.send_message(ctx.message.channel, fmt.format(type(e).__name__, e))
        else:
            player.volume = 0.6
            entry = VoiceEntry(ctx.message, player)
            await self.bot.say('Enqueued ' + str(entry))
            await state.songs.put(entry)
            self.list += [song]
            try:
                with open("songs.txt", "a") as fp:
                    

                    fp.write(song + "\n")
            except Exception as e:
                await self.bot.say(e)
            fp.close()


    @commands.command(pass_context=True, no_pm=True)
    async def volume(self, ctx, value : int):
        """Sets the volume of the currently playing song."""

        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.volume = value / 100
            await self.bot.say('Set the volume to {:.0%}'.format(player.volume))

    @commands.command(pass_context=True, no_pm=True)
    async def pause(self, ctx):
        """Pauses the currently played song."""
        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.pause()

    @commands.command(pass_context=True, no_pm=True)
    async def resume(self, ctx):
        """Resumes the currently played song."""
        state = self.get_voice_state(ctx.message.server)
        if state.is_playing():
            player = state.player
            player.resume()

    @commands.command(pass_context=True, no_pm=True)
    async def stop(self, ctx):
        """Stops playing audio and leaves the voice channel.
        This also clears the queue.
        """
        server = ctx.message.server
        state = self.get_voice_state(server)
        self.autoplay = "off"

        if state.is_playing():
            player = state.player
            player.stop()

        try:
            state.audio_player.cancel()
            del self.voice_states[server.id]
            await state.voice.disconnect()
        except:
            pass

    @commands.command(pass_context=True, no_pm=True)
    async def skip(self, ctx):
        """Vote to skip a song. The song requester can automatically skip.
        3 skip votes are needed for the song to be skipped.
        """        	

        state = self.get_voice_state(ctx.message.server)
        if not state.is_playing():
            await self.bot.say('Not playing any music right now...')
            return


        for x in ctx.message.author.roles:
            if("DJ" == x.name.upper() or "PRO" == x.name.upper()):
                await self.bot.say("Skipped")
                state.skip()
                return

        if(state.current.requester.name == "DakBot"):
        	await self.bot.say("Skipped")
        	state.skip()
        	return


        voter = ctx.message.author
        if voter == state.current.requester:
            await self.bot.say('Requester requested skipping song...')
            state.skip()

        elif voter.id not in state.skip_votes:
            state.skip_votes.add(voter.id)
            total_votes = len(state.skip_votes)
            if total_votes >= 3:
                await self.bot.say('Skip vote passed, skipping song...')
                state.skip()
            else:
                await self.bot.say('Skip vote added, currently at [{}/3]'.format(total_votes))
        else:
            await self.bot.say('You have already voted to skip this song.')

    @commands.command(pass_context=True, no_pm=True)
    async def np(self, ctx):
        """Shows info about the currently played song."""

        state = self.get_voice_state(ctx.message.server)
        if state.current is None:
            await self.bot.say('Not playing anything.')
        else:
            skip_count = len(state.skip_votes)
            await self.bot.say('Now playing {} [skips: {}/3]'.format(state.current, skip_count))


    @commands.command(pass_context=True, no_pm=True)
    async def queue(self, ctx):
    	counter = 0

    	if(len(self.get_voice_state(ctx.message.server).songs._queue) == 0 ):
    		await self.bot.say("No songs queued")
    		return


    	for x in self.get_voice_state(ctx.message.server).songs._queue:
    		counter += 1
    		await self.bot.say(str(counter) + ": " + str(x))


    @commands.command(pass_context=True, no_pm=True)
    async def remove(self, ctx, *, song : str):
    	if(song.isdigit()):
    		num = int(song)
    		num -= 1
	    	if(num < 0 or num > len(self.get_voice_state(ctx.message.server).songs._queue) ):
	    		await self.bot.say("There is no song corresponding to this number")
	    		return
	    		
	    	text = self.get_voice_state(ctx.message.server).songs._queue[num]
	    	del self.get_voice_state(ctx.message.server).songs._queue[num]
	    	await self.bot.say(str(text) + "has been removed")

    	else:
	    	song = song.split(" ")
	    	temp = []
	    	queue = list(self.get_voice_state(ctx.message.server).songs._queue)
	    	for word in song:
	    		for title in queue:
	    			await self.bot.say(str(word))
	    			if(word.lower() in str(title).lower()):
	    				temp += [title]

	    		queue = list(temp)
	    		temp = []
	    	if(len(queue) == 0):
	    		await self.bot.say("Could not find song matching that pattern")
	    		return
	    	await self.bot.say(len(queue))
	    	if(len(queue) > 1):
		    	temp = []
		    	while(song in queue):
		    		temp += [song]
		    		queue.remove(song)
		    	if(len(temp) != 1):
		    		await self.bot.say("Can't determine song")
		    		for x in temp:
		    			await self.bot.say(str(x))
		    		return
		    	await self.bot.say(str(temp[0]))
	    		self.get_voice_state(ctx.message.server).songs._queue.remove(temp[0])
	    		await self.bot.say("Removing " + str(temp[0]))
	    		return
	    	if(len(queue) == 1):
	    		self.get_voice_state(ctx.message.server).songs._queue.remove(queue[0])
	    		await self.bot.say("Removing "+str(queue[0]))
	    		return


    
    

    @commands.command(pass_context=True, no_pm=True)
    async def startautoplay(self, ctx):
            self.autoplay = "on"
 
            #await self.bot.say(ctx.message.author)
            #ctx.message.author = "DakBot#6722"
            while(True):
                if(self.autoplay != "on"):
                    break
                songs = len(self.get_voice_state(ctx.message.server).songs._queue)
                if( songs  < 3  and len(self.list) > 1):
                    state = self.get_voice_state(ctx.message.server)
                    if state.voice is None:
                        success = await ctx.invoke(self.summon)
                        if not success:
                            return

                    
                    opts = {
                        'default_search': 'auto',
                        'quiet': True,
                        }

                    song = random.choice(self.list)

                    try:
                        await self.bot.say("queueing new song")
                        player = await state.voice.create_ytdl_player(song, ytdl_options=opts, after=state.toggle_next)
                        if(player.error != None):
                            await self.bot.say(player.error)

                    except Exception as e:
                        fmt = 'An error occurred while processing this request: ```py\n{}: {}\n```'
                        await self.bot.send_message(ctx.message.channel, fmt.format(type(e).__name__, e))
                    else:
                        ctx.message.timestamp = datetime.now()
                        entry = VoiceEntry(ctx.message, player)
                        await self.bot.say('Enqueued ' + str(entry))
                        await state.songs.put(entry)
                        #await asyncio.sleep(5)

                else:
                    await asyncio.sleep(5)

    #    t2.start()



    @commands.command(pass_context=True, no_pm=True)
    async def stopautoplaylist(self):
        self.autoplay = "off"


    @commands.command(pass_context=True, no_pm=True)
    async def move(self, ctx, song : int, location : int):

        element = self.get_voice_state(ctx.message.server).songs._queue[song - 1]

        self.get_voice_state(ctx.message.server).songs._queue.insert(location - 1, element)



        del self.get_voice_state(ctx.message.server).songs._queue[song]

bot = commands.Bot(command_prefix=commands.when_mentioned_or('$'), description='A playlist example for discord.py')
bot.add_cog(Music(bot))

@bot.event
async def on_ready():
    print('Logged in as:\n{0} (ID: {0.id})'.format(bot.user))


bot.run('NDY4OTIzMDEzNjk0MjI2NDM0.Dk6IFw.I4PL9pPks2iqMnun6A9lkGyBkPE')

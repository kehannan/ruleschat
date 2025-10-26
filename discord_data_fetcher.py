import discord
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

class DiscordDataFetcher:
    def __init__(self, token: str):
        self.token = token
        self.client = discord.Client(intents=discord.Intents.default())
        
    async def fetch_channel_messages(self, channel_id: int, limit: int = 1000, days_back: int = 30) -> List[Dict[str, Any]]:
        """
        Fetch messages from a Discord channel
        
        Args:
            channel_id: The Discord channel ID
            limit: Maximum number of messages to fetch
            days_back: How many days back to fetch messages from
            
        Returns:
            List of message dictionaries
        """
        messages = []
        
        @self.client.event
        async def on_ready():
            print(f'Logged in as {self.client.user}')
            
            try:
                channel = self.client.get_channel(channel_id)
                if not channel:
                    print(f"Channel with ID {channel_id} not found")
                    await self.client.close()
                    return
                
                print(f"Fetching messages from #{channel.name}")
                
                # Calculate the time limit
                time_limit = datetime.utcnow() - timedelta(days=days_back)
                
                # Fetch messages
                async for message in channel.history(limit=limit, after=time_limit):
                    # Skip bot messages and system messages
                    if message.author.bot or message.type != discord.MessageType.default:
                        continue
                    
                    # Extract message data
                    message_data = {
                        'id': str(message.id),
                        'content': message.content,
                        'author': {
                            'id': str(message.author.id),
                            'name': message.author.display_name,
                            'username': message.author.name
                        },
                        'timestamp': message.created_at.isoformat(),
                        'channel_id': str(message.channel.id),
                        'channel_name': message.channel.name,
                        'guild_id': str(message.guild.id) if message.guild else None,
                        'guild_name': message.guild.name if message.guild else None,
                        'attachments': [att.url for att in message.attachments],
                        'embeds': len(message.embeds),
                        'reactions': [{'emoji': str(reaction.emoji), 'count': reaction.count} 
                                    for reaction in message.reactions]
                    }
                    
                    messages.append(message_data)
                    
                    # Print progress
                    if len(messages) % 100 == 0:
                        print(f"Fetched {len(messages)} messages...")
                
                print(f"Successfully fetched {len(messages)} messages")
                
            except Exception as e:
                print(f"Error fetching messages: {e}")
            finally:
                await self.client.close()
        
        await self.client.start(self.token)
        return messages
    
    def save_messages_to_json(self, messages: List[Dict[str, Any]], filename: str = "discord_messages.json"):
        """Save messages to a JSON file"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(messages, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(messages)} messages to {filename}")
    
    def save_messages_to_jsonl(self, messages: List[Dict[str, Any]], filename: str = "discord_messages.jsonl"):
        """Save messages to a JSONL file (one JSON object per line)"""
        with open(filename, 'w', encoding='utf-8') as f:
            for message in messages:
                f.write(json.dumps(message, ensure_ascii=False) + '\n')
        print(f"Saved {len(messages)} messages to {filename}")
    
    def convert_to_qa_format(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert Discord messages to a Q&A training format
        This creates conversation pairs that can be used for training
        """
        qa_pairs = []
        
        for i in range(len(messages) - 1):
            current_msg = messages[i]
            next_msg = messages[i + 1]
            
            # Create a Q&A pair
            qa_pair = {
                'question': current_msg['content'],
                'answer': next_msg['content'],
                'context': {
                    'question_author': current_msg['author']['name'],
                    'answer_author': next_msg['author']['name'],
                    'timestamp': current_msg['timestamp'],
                    'channel': current_msg['channel_name']
                }
            }
            
            qa_pairs.append(qa_pair)
        
        return qa_pairs

async def main():
    """Main function to run the Discord data fetcher"""
    # Get Discord bot token from environment
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("Error: DISCORD_BOT_TOKEN not found in environment variables")
        print("Please set your Discord bot token in your .env file")
        return
    
    # Channel ID from the URL: https://discord.com/channels/415876852419395586/1006302166094450769
    # The last part is the channel ID
    channel_id = 1006302166094450769
    
    # Create fetcher instance
    fetcher = DiscordDataFetcher(token)
    
    try:
        # Fetch messages
        print("Starting to fetch Discord messages...")
        messages = await fetcher.fetch_channel_messages(
            channel_id=channel_id,
            limit=1000,  # Adjust as needed
            days_back=30  # Adjust as needed
        )
        
        if messages:
            # Save raw messages
            fetcher.save_messages_to_json(messages, "discord_raw_messages.json")
            fetcher.save_messages_to_jsonl(messages, "discord_raw_messages.jsonl")
            
            # Convert to Q&A format
            qa_pairs = fetcher.convert_to_qa_format(messages)
            fetcher.save_messages_to_json(qa_pairs, "discord_qa_pairs.json")
            fetcher.save_messages_to_jsonl(qa_pairs, "discord_qa_pairs.jsonl")
            
            print(f"\nSummary:")
            print(f"- Raw messages: {len(messages)}")
            print(f"- Q&A pairs: {len(qa_pairs)}")
            print(f"- Files created: discord_raw_messages.json, discord_raw_messages.jsonl")
            print(f"- Files created: discord_qa_pairs.json, discord_qa_pairs.jsonl")
        else:
            print("No messages were fetched")
            
    except Exception as e:
        print(f"Error in main: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 
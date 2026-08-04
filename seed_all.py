import asyncio
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app.db.session import get_session_maker
from app.models.user import User
from app.models.campaign import Campaign
from app.models.customer import Customer
from app.models.campaign_lead import CampaignLead
from app.models.document import Document
from app.services.security import hash_password
from app.services.workflow_service import WorkflowService
from app.services.rag_service import RAGService

# Create folders if not exist
DOCS_DIR = os.path.join("data", "documents")
os.makedirs(DOCS_DIR, exist_ok=True)

# 1. Operational RAG documents data
HOSPITAL_DOCS = {
    "appointment_policy.txt": (
        "Mercy Hospital Appointment Policy:\n"
        "Appointments can be scheduled online, via phone, or through our AI patient portal.\n"
        "Patients are requested to arrive 15 minutes before their scheduled time slot.\n"
        "Please bring a valid photo ID and insurance card for registration."
    ),
    "cancellation_policy.txt": (
        "Mercy Hospital Cancellation & No-Show Policy:\n"
        "Cancellations must be made at least 24 hours in advance.\n"
        "No-shows or cancellations within 24 hours may incur a convenience fee of $25.\n"
        "To cancel or reschedule, please use the automated calling system or speak to a coordinator."
    ),
    "departments.txt": (
        "Mercy Hospital Specialized Departments:\n"
        "1. Cardiology - Heart disease prevention, diagnosis, and treatment.\n"
        "2. Pediatrics - Comprehensive healthcare services for infants and kids.\n"
        "3. Orthopedics - Musculoskeletal system treatments, bone care, and joints.\n"
        "4. Neurology - Nervous system disorders, brain injury, and spinal care."
    ),
    "doctor_timings.txt": (
        "Mercy Hospital Doctor Timings and OPD Hours:\n"
        "- Dr. Emily Vance (Cardiology): Mon, Wed, Fri from 9:00 AM to 1:00 PM.\n"
        "- Dr. Sanjay Gupta (Pediatrics): Tue, Thu from 10:00 AM to 4:00 PM.\n"
        "- Dr. Rajesh Kumar (Orthopedics): Daily from 2:00 PM to 6:00 PM.\n"
        "- Dr. Lakshmi Naidu (Neurology): Wed, Thu from 1:00 PM to 5:00 PM."
    ),
    "insurance_info.txt": (
        "Mercy Hospital Accepted Insurances:\n"
        "We accept major national insurance plans including Blue Shield, Aetna, Cigna, Medicare,\n"
        "and UnitedHealthcare. Please verify network coverage details with your plan provider beforehand."
    ),
    "general_faq.txt": (
        "Mercy Hospital General FAQ:\n"
        "Q: Is parking free?\n"
        "A: Yes, free parking is available for all patients and visitors in the East Wing lot.\n"
        "Q: Does the hospital have a pharmacy?\n"
        "A: Yes, our 24/7 outpatient pharmacy is located in the Main Lobby."
    ),
    "visiting_hours.txt": (
        "Mercy Hospital Visitor Guidelines and Hours:\n"
        "Visiting hours are from 10:00 AM to 8:00 PM daily.\n"
        "Only two visitors are permitted per patient room at any one time.\n"
        "Visitors must sign in at the reception desk."
    ),
    "emergency_info.txt": (
        "Mercy Hospital Emergency & Trauma Care:\n"
        "Our Level 1 Trauma Center is open 24/7/365.\n"
        "For critical life-threatening conditions, please call 911 immediately.\n"
        "The emergency entrance is located on Sector 5, Medical Drive."
    )
}

REAL_ESTATE_DOCS = {
    "property_brochure.txt": (
        "Premium Realty - Orchard Heights Brochure:\n"
        "Orchard Heights is a premium residential community offering luxury 2 BHK and 3 BHK apartments.\n"
        "Located in the serene green corridor of Gachibowli, Hyderabad.\n"
        "Spanning over 15 acres of landscaped gardens with a massive central clubhouse."
    ),
    "pricing_details.txt": (
        "Orchard Heights Pricing Guide:\n"
        "- Luxury 2 BHK: Starting from $120,000 (Built-up area: 1,200 sq.ft).\n"
        "- Ultra 3 BHK: Starting from $175,000 (Built-up area: 1,800 sq.ft).\n"
        "Prices exclude stamp duty, registration charges, and maintenance reserves."
    ),
    "amenities_info.txt": (
        "Orchard Heights Amenities List:\n"
        "- Five-star Clubhouse with infinity pool, spa, and gymnasium.\n"
        "- 2 km jogging and cycling track with child-safe play areas.\n"
        "- Indoor sports complex including badminton, squash, and table tennis.\n"
        "- 24/7 multi-tier smart security with app-controlled guest access."
    ),
    "booking_policy.txt": (
        "Orchard Heights Booking & Allotment Policy:\n"
        "Standard expression of interest (EOI) booking amount is $5,000.\n"
        "Allotment of units is subject to verification and credit check.\n"
        "Initial booking amount is fully refundable within 15 days of reservation."
    ),
    "loan_faq.txt": (
        "Orchard Heights Home Loan FAQ:\n"
        "Q: Do you offer pre-approved home loan options?\n"
        "A: Yes, we are associated with HDFC Bank, ICICI Bank, and SBI for instant processing.\n"
        "Q: What is the maximum loan tenure available?\n"
        "A: Up to 30 years, subject to eligibility criteria."
    ),
    "site_visit_policy.txt": (
        "Orchard Heights Site Visit Guidelines:\n"
        "Physical site showings are conducted daily from 9:00 AM to 6:00 PM.\n"
        "Free pickup and drop services are provided for pre-qualified buyers within a 15 km radius.\n"
        "Please schedule your showing at least 4 hours in advance."
    ),
    "possession_timeline.txt": (
        "Orchard Heights Construction & Possession Timeline:\n"
        "Phase 1 (Towers A, B, C): Possession starting December 2026.\n"
        "Phase 2 (Towers D, E, F): Possession starting June 2027.\n"
        "RERA Registration number: RERA-HYD-10294-2025."
    ),
    "refund_policy.txt": (
        "Orchard Heights Cancellation & Refund Terms:\n"
        "Cancellations before agreement: Booking fee refunded with 1% processing fee deduction.\n"
        "Cancellations post-agreement: Refund is subject to forfeiture of the 10% earnest money deposit."
    )
}

# 2. Customers Data
CUSTOMERS = [
    # Hospital Customers
    {"first_name": "Rahul", "last_name": "Sharma", "phone_number": "+919876543210", "email": "rahul.sharma@example.com", "lang": "Hindi", "doctor": "Dr. Emily Vance", "dept": "Cardiology", "date": "2026-08-05", "time": "10:00 AM"},
    {"first_name": "Ananya", "last_name": "Reddy", "phone_number": "+918765432109", "email": "ananya.r@example.com", "lang": "Telugu", "doctor": "Dr. Sanjay Gupta", "dept": "Pediatrics", "date": "2026-08-06", "time": "11:30 AM"},
    {"first_name": "John", "last_name": "Doe", "phone_number": "+15551234567", "email": "john.doe@example.com", "lang": "English", "doctor": "Dr. Rajesh Kumar", "dept": "Orthopedics", "date": "2026-08-05", "time": "03:00 PM"},
    {"first_name": "Vikram", "last_name": "Patel", "phone_number": "+917654321098", "email": "vikram.patel@example.com", "lang": "Hindi", "doctor": "Dr. Lakshmi Naidu", "dept": "Neurology", "date": "2026-08-07", "time": "02:00 PM"},
    {"first_name": "Srinivas", "last_name": "Rao", "phone_number": "+916543210987", "email": "sri.rao@example.com", "lang": "Telugu", "doctor": "Dr. Emily Vance", "dept": "Cardiology", "date": "2026-08-08", "time": "09:30 AM"},
    {"first_name": "Alice", "last_name": "Smith", "phone_number": "+15552345678", "email": "alice.smith@example.com", "lang": "English", "doctor": "Dr. Sanjay Gupta", "dept": "Pediatrics", "date": "2026-08-06", "time": "04:00 PM"},
    {"first_name": "Amit", "last_name": "Verma", "phone_number": "+919543210987", "email": "amit.verma@example.com", "lang": "Hindi", "doctor": "Dr. Rajesh Kumar", "dept": "Orthopedics", "date": "2026-08-05", "time": "10:30 AM"},
    {"first_name": "Kalyani", "last_name": "Krishna", "phone_number": "+918543210987", "email": "kalyani.k@example.com", "lang": "Telugu", "doctor": "Dr. Lakshmi Naidu", "dept": "Neurology", "date": "2026-08-09", "time": "01:30 PM"},
    {"first_name": "Robert", "last_name": "Johnson", "phone_number": "+15553456789", "email": "robert.j@example.com", "lang": "English", "doctor": "Dr. Emily Vance", "dept": "Cardiology", "date": "2026-08-05", "time": "11:00 AM"},
    {"first_name": "Priyesh", "last_name": "Mishra", "phone_number": "+917543210987", "email": "priyesh.m@example.com", "lang": "Hindi", "doctor": "Dr. Sanjay Gupta", "dept": "Pediatrics", "date": "2026-08-06", "time": "02:30 PM"},
    {"first_name": "Venkatesh", "last_name": "Prasad", "phone_number": "+916543210777", "email": "venky.p@example.com", "lang": "Telugu", "doctor": "Dr. Rajesh Kumar", "dept": "Orthopedics", "date": "2026-08-07", "time": "03:30 PM"},
    {"first_name": "Emily", "last_name": "Davis", "phone_number": "+15554567890", "email": "emily.d@example.com", "lang": "English", "doctor": "Dr. Lakshmi Naidu", "dept": "Neurology", "date": "2026-08-08", "time": "10:00 AM"},
    
    # Real Estate Customers
    {"first_name": "Sandeep", "last_name": "Rathore", "phone_number": "+919812345678", "email": "sandeep.r@example.com", "lang": "Hindi", "prop": "Orchard Heights", "budget": "$130,000", "loc": "Gachibowli", "status": "Warm Lead"},
    {"first_name": "Harini", "last_name": "Shetty", "phone_number": "+918812345678", "email": "harini.s@example.com", "lang": "Telugu", "prop": "Orchard Heights", "budget": "$180,000", "loc": "Gachibowli", "status": "Hot Lead"},
    {"first_name": "David", "last_name": "Miller", "phone_number": "+15555678901", "email": "david.miller@example.com", "lang": "English", "prop": "Orchard Heights", "budget": "$125,000", "loc": "Gachibowli", "status": "New Lead"},
    {"first_name": "Yash", "last_name": "Goyal", "phone_number": "+917812345678", "email": "yash.g@example.com", "lang": "Hindi", "prop": "Orchard Heights", "budget": "$190,000", "loc": "Gachibowli", "status": "Warm Lead"},
    {"first_name": "Divya", "last_name": "Teja", "phone_number": "+916812345678", "email": "divya.t@example.com", "lang": "Telugu", "prop": "Orchard Heights", "budget": "$140,000", "loc": "Gachibowli", "status": "Hot Lead"},
    {"first_name": "Sarah", "last_name": "Connor", "phone_number": "+15556789012", "email": "sarah.c@example.com", "lang": "English", "prop": "Orchard Heights", "budget": "$150,000", "loc": "Gachibowli", "status": "Warm Lead"},
    {"first_name": "Rajesh", "last_name": "Gupta", "phone_number": "+919998887776", "email": "rajesh.gupta@example.com", "lang": "Hindi", "prop": "Orchard Heights", "budget": "$175,000", "loc": "Gachibowli", "status": "Hot Lead"},
    {"first_name": "Pranitha", "last_name": "Rao", "phone_number": "+918887776665", "email": "pranitha.r@example.com", "lang": "Telugu", "prop": "Orchard Heights", "budget": "$135,000", "loc": "Gachibowli", "status": "New Lead"}
]

async def seed_documents(session, campaign_id, docs_dict):
    """Write text files and index them into ChromaDB knowledge base."""
    rag_service = RAGService()
    await rag_service.initialize_collection()
    
    for filename, content in docs_dict.items():
        # 1. Write file
        file_path = os.path.join(DOCS_DIR, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        # 2. Insert record in PostgreSQL
        document = Document(
            id=uuid.uuid4(),
            campaign_id=campaign_id,
            filename=filename,
            file_type="txt",
            status="active",
            total_chunks=0
        )
        session.add(document)
        await session.flush()
        
        # 3. Index in ChromaDB
        chunks_indexed = await rag_service.index_document(
            campaign_id=campaign_id,
            document_id=document.id,
            filename=filename,
            text=content
        )
        
        # Update chunk counts
        document.total_chunks = chunks_indexed
        print(f"Indexed doc '{filename}' into ChromaDB for campaign {campaign_id} ({chunks_indexed} chunks).")

async def main():
    print("Initializing Platform Database and RAG Seeder...")
    async_session_maker = get_session_maker()
    async with async_session_maker() as session:
        # Step 1: Seed admin user
        admin_query = select(User).where(User.email == "admin@example.com")
        admin_res = await session.execute(admin_query)
        admin = admin_res.scalars().first()
        if not admin:
            admin = User(
                email="admin@example.com",
                hashed_password=hash_password("admin123"),
                role="admin",
                is_active=True
            )
            session.add(admin)
            print("Seeded administrator: admin@example.com / admin123")
        else:
            print("Administrator admin@example.com already exists.")
            
        # Step 2: Seed campaigns
        # Define 3 Hospital and 2 Real Estate Campaigns
        campaign_definitions = [
            # Hospital
            {"name": "Hospital: Appointment Reminder", "desc": "Send automated voice reminders to patients about upcoming consults.", "workflow": "hospital", "retries": 3, "interval": 60},
            {"name": "Hospital: Appointment Confirmation", "desc": "Call patients to record explicit confirmations for doctor visits.", "workflow": "hospital", "retries": 2, "interval": 30},
            {"name": "Hospital: Appointment Rescheduling", "desc": "Engage patients who requested alternate appointment date/time slots.", "workflow": "hospital", "retries": 4, "interval": 90},
            
            # Real Estate
            {"name": "Real Estate: Lead Qualification", "desc": "Qualify inbound property interests based on budget and buy intent.", "workflow": "real_estate", "retries": 3, "interval": 60},
            {"name": "Real Estate: Site Visit Booking", "desc": "Connect with hot prospects to schedule physical showings at Orchard Heights.", "workflow": "real_estate", "retries": 2, "interval": 45}
        ]
        
        seeded_campaigns = []
        workflow_service = WorkflowService(session)
        
        for cdef in campaign_definitions:
            # Check if campaign already exists
            camp_query = select(Campaign).where(Campaign.name == cdef["name"])
            camp_res = await session.execute(camp_query)
            campaign = camp_res.scalars().first()
            
            if not campaign:
                campaign = Campaign(
                    id=uuid.uuid4(),
                    name=cdef["name"],
                    description=cdef["desc"],
                    workflow_type=cdef["workflow"],
                    status="active",
                    max_retries=cdef["retries"],
                    retry_interval_minutes=cdef["interval"],
                    is_active=True
                )
                session.add(campaign)
                await session.flush()
                # Seed defaults
                await workflow_service.seed_campaign_defaults(campaign.id, campaign.workflow_type)
                print(f"Created Campaign: {campaign.name}")
            else:
                print(f"Campaign already exists: {campaign.name}")
                
            seeded_campaigns.append(campaign)
            
        await session.commit()
        
        # Step 3: Seed customers & create leads
        print("Seeding customer database and campaign leads...")
        hospital_campaigns = [c for c in seeded_campaigns if c.workflow_type == "hospital"]
        re_campaigns = [c for c in seeded_campaigns if c.workflow_type == "real_estate"]
        
        for idx, cust_data in enumerate(CUSTOMERS):
            # Check if customer exists by phone
            cust_query = select(Customer).where(Customer.phone_number == cust_data["phone_number"])
            cust_res = await session.execute(cust_query)
            customer = cust_res.scalars().first()
            
            # Custom variables dictionary setup
            custom_vars = {
                "preferred_language": cust_data["lang"]
            }
            if "doctor" in cust_data:
                custom_vars.update({
                    "doctor_name": cust_data["doctor"],
                    "department": cust_data["dept"],
                    "appointment_date": cust_data["date"],
                    "appointment_time": cust_data["time"]
                })
            else:
                custom_vars.update({
                    "property_interest": cust_data["prop"],
                    "budget": cust_data["budget"],
                    "location": cust_data["loc"],
                    "lead_status": cust_data["status"]
                })
                
            if not customer:
                customer = Customer(
                    id=uuid.uuid4(),
                    first_name=cust_data["first_name"],
                    last_name=cust_data["last_name"],
                    phone_number=cust_data["phone_number"],
                    email=cust_data["email"],
                    custom_variables=custom_vars,
                    is_active=True
                )
                session.add(customer)
                await session.flush()
                print(f"Inserted Customer: {customer.first_name} {customer.last_name or ''}")
            else:
                # Update custom variables
                customer.custom_variables = custom_vars
                session.add(customer)
                print(f"Customer already exists: {customer.first_name}")
                
            # Distribute customer leads across campaigns
            # First 12 customers to hospital campaigns, next 8 to real estate campaigns
            target_campaign = None
            if idx < 12:
                # Round-robin hospital campaigns
                target_campaign = hospital_campaigns[idx % len(hospital_campaigns)]
            else:
                # Round-robin real estate campaigns
                target_campaign = re_campaigns[(idx - 12) % len(re_campaigns)]
                
            # Create CampaignLead link
            lead_query = select(CampaignLead).where(
                CampaignLead.campaign_id == target_campaign.id,
                CampaignLead.customer_id == customer.id
            )
            lead_res = await session.execute(lead_query)
            lead = lead_res.scalars().first()
            
            if not lead:
                lead = CampaignLead(
                    campaign_id=target_campaign.id,
                    customer_id=customer.id,
                    status="pending"
                )
                session.add(lead)
                print(f"Linked lead: {customer.first_name} -> {target_campaign.name}")
                
        await session.commit()
        
        # Step 4: Write and index RAG documents
        print("Writing and indexing RAG knowledge documents...")
        for campaign in seeded_campaigns:
            if campaign.workflow_type == "hospital":
                await seed_documents(session, campaign.id, HOSPITAL_DOCS)
            else:
                await seed_documents(session, campaign.id, REAL_ESTATE_DOCS)
                
        # Step 5: Seed voice profiles
        await seed_voice_profiles(session)

        await session.commit()
        print("Platform Seeding Completed Successfully!")

VOICE_PROFILES = [
    {
        "name": "Sophia",
        "description": "Professional Female",
        "avatar": "/static/images/avatars/sophia.png",
        "gender": "Female",
        "supported_languages": "English,Hindi,Telugu",
        "voice_provider": "melotts",
        "voice_configuration": '{"speaker_id": "EN_INDIA", "speed": 1.0}',
        "preview_audio": "/static/audio/previews/sophia.mp3",
        "status": "active"
    },
    {
        "name": "Maya",
        "description": "Friendly Female",
        "avatar": "/static/images/avatars/maya.png",
        "gender": "Female",
        "supported_languages": "English,Hindi",
        "voice_provider": "melotts",
        "voice_configuration": '{"speaker_id": "EN_US", "speed": 1.0}',
        "preview_audio": "/static/audio/previews/maya.mp3",
        "status": "active"
    },
    {
        "name": "Ananya",
        "description": "Customer Support Female",
        "avatar": "/static/images/avatars/ananya.png",
        "gender": "Female",
        "supported_languages": "English,Telugu",
        "voice_provider": "melotts",
        "voice_configuration": '{"speaker_id": "EN_INDIA", "speed": 1.05}',
        "preview_audio": "/static/audio/previews/ananya.mp3",
        "status": "active"
    },
    {
        "name": "Arjun",
        "description": "Professional Male",
        "avatar": "/static/images/avatars/arjun.png",
        "gender": "Male",
        "supported_languages": "English,Hindi,Telugu",
        "voice_provider": "melotts",
        "voice_configuration": '{"speaker_id": "EN_INDIA", "speed": 1.0}',
        "preview_audio": "/static/audio/previews/arjun.mp3",
        "status": "active"
    },
    {
        "name": "David",
        "description": "Sales Consultant Male",
        "avatar": "/static/images/avatars/david.png",
        "gender": "Male",
        "supported_languages": "English",
        "voice_provider": "melotts",
        "voice_configuration": '{"speaker_id": "EN_US", "speed": 1.0}',
        "preview_audio": "/static/audio/previews/david.mp3",
        "status": "active"
    }
]

async def seed_voice_profiles(session):
    from app.voice_demo.models.voice_profile import VoiceProfile
    from sqlalchemy import select

    print("Seeding voice profiles...")
    for vdata in VOICE_PROFILES:
        q = select(VoiceProfile).where(VoiceProfile.name == vdata["name"])
        res = await session.execute(q)
        vp = res.scalars().first()
        if not vp:
            vp = VoiceProfile(
                name=vdata["name"],
                description=vdata["description"],
                avatar=vdata["avatar"],
                gender=vdata["gender"],
                supported_languages=vdata["supported_languages"],
                voice_provider=vdata["voice_provider"],
                voice_configuration=vdata["voice_configuration"],
                preview_audio=vdata["preview_audio"],
                status=vdata["status"]
            )
            session.add(vp)
            print(f"Created Voice Profile: {vp.name}")
        else:
            vp.description = vdata["description"]
            vp.avatar = vdata["avatar"]
            vp.gender = vdata["gender"]
            vp.supported_languages = vdata["supported_languages"]
            vp.voice_provider = vdata["voice_provider"]
            vp.voice_configuration = vdata["voice_configuration"]
            vp.preview_audio = vdata["preview_audio"]
            vp.status = vdata["status"]
            session.add(vp)
            print(f"Updated Voice Profile: {vp.name}")

if __name__ == "__main__":
    asyncio.run(main())
